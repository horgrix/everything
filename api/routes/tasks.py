"""Task management endpoints."""

import time
import logging
import yaml
import os
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from crawler.engine import CrawlerEngine
from task_manager.loader import TaskLoader

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db(request: Request):
    return request.app.state.db


def _get_config_dir(request: Request) -> str:
    return request.app.state.config_dir


def _load_tasks(request: Request) -> list[dict]:
    loader = TaskLoader(_get_config_dir(request), _get_db(request))
    return loader.load_all()

def _task_filepath(config_dir: str, task_name: str, trigger_type: str = "system") -> str:
    """根据 trigger_type 确定 YAML 文件存放路径"""
    sub = "system_trigger" if trigger_type == "system" else "user_trigger"
    return os.path.join(config_dir, sub, f"{task_name}.yaml")


@router.get("")
async def list_tasks(request: Request):
    """List all tasks."""
    tasks = _load_tasks(request)
    result = []
    for t in tasks:
        # 从 outputs 中提取所有目标表名
        output_tables = [
            o.get("target_table", "")
            for o in t.get("outputs", [])
            if o.get("target_table")
        ]
        result.append({
            "name": t.get("name"),
            "type": t.get("type"),
            "schedule": t.get("schedule"),
            "target_tables": output_tables,
            "trigger_type": t.get("_trigger_type", "system"),
            "enabled": t.get("enabled", True),
        })
    return {"code": 0, "message": "success", "data": result}


@router.get("/{task_name}")
async def get_task(request: Request, task_name: str):
    """Get a single task config, including raw config_yaml from DB."""
    db = _get_db(request)
    row = db.conn.execute(
        "SELECT config_yaml FROM crawl_tasks WHERE task_name = ?", (task_name,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")

    # Also load parsed task config
    tasks = _load_tasks(request)
    parsed = None
    for t in tasks:
        if t.get("name") == task_name:
            parsed = t
            break

    return {
        "code": 0,
        "message": "success",
        "data": {
            **(parsed or {}),
            "config_yaml": row["config_yaml"],
        },
    }


@router.post("/{task_name}/run")
async def trigger_run(request: Request, task_name: str):
    """Trigger a task to run immediately."""
    tasks = _load_tasks(request)
    db = _get_db(request)

    target = None
    for t in tasks:
        if t.get("name") == task_name:
            target = t
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")

    # 从数据库获取真实 task_id，避免 FOREIGN KEY 约束失败
    task_row = db.conn.execute(
        "SELECT id FROM crawl_tasks WHERE task_name = ?", (task_name,)
    ).fetchone()
    if task_row is None:
        from task_manager.loader import TaskLoader
        loader = TaskLoader(_get_config_dir(request), db)
        target = loader._register_task(target)
    task_id = task_row["id"] if task_row else target.get("_task_id", 0)
    log_id = db.start_crawl_log(task_id)

    start = time.time()
    engine = CrawlerEngine()

    try:
        stats = await engine.run(target, db)
        duration_ms = int((time.time() - start) * 1000)
        if stats.get("error"):
            db.fail_crawl_log(log_id, stats["error"], duration_ms)
        else:
            db.finish_crawl_log(
                log_id,
                records_new=stats.get("new", 0),
                records_updated=stats.get("updated", 0),
                records_skipped=stats.get("skipped", 0),
                duration_ms=duration_ms,
            )
        return {
            "code": 0,
            "message": "success",
            "data": {**stats, "duration_ms": duration_ms, "log_id": log_id},
        }
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        db.fail_crawl_log(log_id, str(e), duration_ms)
        raise HTTPException(status_code=500, detail=str(e))


class CreateTaskRequest(BaseModel):
    name: str
    config_yaml: str


@router.delete("/{task_name}")
async def delete_task(request: Request, task_name: str):
    """
    删除任务：从调度器移除 + 删除 YAML 文件 + 从数据库删除。
    """
    config_dir = _get_config_dir(request)
    db = _get_db(request)

    # 获取 trigger_type 用于定位文件
    task_row = db.conn.execute(
        "SELECT trigger_type FROM crawl_tasks WHERE task_name = ?", (task_name,)
    ).fetchone()
    trigger_type = task_row["trigger_type"] if task_row else "system"

    # 1. 从调度器移除
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        try:
            scheduler._scheduler.remove_job(task_name)
        except Exception:
            pass

    # 2. 删除 YAML 文件（仅子目录）
    deleted_files = []
    for sub in ("system_trigger", "user_trigger"):
        fp = os.path.join(config_dir, sub, f"{task_name}.yaml")
        if os.path.exists(fp):
            os.remove(fp)
            deleted_files.append(fp)

    # 3. 从数据库删除
    db.conn.execute("DELETE FROM crawl_tasks WHERE task_name = ?", (task_name,))
    db.conn.commit()

    return {
        "code": 0,
        "message": f"任务 '{task_name}' 已删除",
        "data": {"name": task_name, "files_deleted": len(deleted_files)},
    }


@router.put("/{task_name}")
async def update_task(request: Request, task_name: str, body: CreateTaskRequest):
    """
    更新任务配置：覆写 YAML 文件 + 重新注册到数据库 + 热重载到调度器。
    """
    yaml_content = body.config_yaml.strip()
    if not yaml_content:
        raise HTTPException(status_code=400, detail="YAML 配置不能为空")

    try:
        task_config = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}")

    if not isinstance(task_config, dict):
        raise HTTPException(status_code=400, detail="YAML 顶层必须是一个字典（任务配置）")

    task_config["name"] = task_name

    config_dir = _get_config_dir(request)
    db = _get_db(request)

    # trigger_type 必填
    trigger_type = task_config.get("trigger_type")
    if not trigger_type:
        raise HTTPException(status_code=400, detail="trigger_type 字段必填（system 或 user）")
    if trigger_type not in ("system", "user"):
        raise HTTPException(status_code=400, detail=f"无效的 trigger_type: {trigger_type}")

    # schedule 校验
    if trigger_type == "system":
        if "schedule" not in task_config or not task_config["schedule"]:
            raise HTTPException(status_code=400, detail="system 类型任务必须包含 schedule 字段")
    else:  # user
        if "schedule" in task_config:
            raise HTTPException(status_code=400, detail="user 类型任务不允许 schedule 字段")

    # 1. 覆写 YAML 文件到对应子目录
    filepath = _task_filepath(config_dir, task_name, trigger_type)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(task_config, f, allow_unicode=True, default_flow_style=False)

    # 2. 重新注册到数据库（upsert）
    from task_manager.loader import TaskLoader
    loader = TaskLoader(config_dir, db)
    processed = loader._register_task(task_config, trigger_type)

    # 3. 热重载到调度器（仅 system 类型）
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler and trigger_type == "system":
        try:
            scheduler._scheduler.remove_job(task_name)
        except Exception:
            pass
        scheduler._register_job(processed)
        scheduler._tasks[task_name] = processed

    return {
        "code": 0,
        "message": f"任务 '{task_name}' 已更新" + ("并热重载" if scheduler and trigger_type == "system" else ""),
        "data": {
            "name": task_name,
            "type": processed.get("type", "web"),
            "schedule": processed.get("schedule"),
            "trigger_type": trigger_type,
            "target_table": processed.get("target_table"),
        },
    }


@router.post("")
async def create_task(request: Request, body: CreateTaskRequest):
    """
    创建新任务：写入 YAML 文件 + 注册到数据库 + 热加载到调度器（仅 system）。
    """
    name = body.name.strip()
    yaml_content = body.config_yaml.strip()

    if not name:
        raise HTTPException(status_code=400, detail="任务名称不能为空")
    if not yaml_content:
        raise HTTPException(status_code=400, detail="YAML 配置不能为空")

    try:
        task_config = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"YAML 解析失败: {e}")

    if not isinstance(task_config, dict):
        raise HTTPException(status_code=400, detail="YAML 顶层必须是一个字典（任务配置）")

    task_config["name"] = name

    # trigger_type 必填
    trigger_type = task_config.get("trigger_type")
    if not trigger_type:
        raise HTTPException(status_code=400, detail="trigger_type 字段必填（system 或 user）")
    if trigger_type not in ("system", "user"):
        raise HTTPException(status_code=400, detail=f"无效的 trigger_type: {trigger_type}")

    # schedule 校验
    if trigger_type == "system":
        if "schedule" not in task_config or not task_config["schedule"]:
            raise HTTPException(status_code=400, detail="system 类型任务必须包含 schedule 字段")
    else:  # user
        if "schedule" in task_config:
            raise HTTPException(status_code=400, detail="user 类型任务不允许 schedule 字段")

    config_dir = _get_config_dir(request)
    db = _get_db(request)

    # 写入 YAML 文件到对应子目录
    filepath = _task_filepath(config_dir, name, trigger_type)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(task_config, f, allow_unicode=True, default_flow_style=False)

    # 注册到数据库
    from task_manager.loader import TaskLoader
    loader = TaskLoader(config_dir, db)
    processed = loader._register_task(task_config, trigger_type)

    # 热加载到调度器（仅 system 类型）
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler and trigger_type == "system":
        scheduler._register_job(processed)
        scheduler._tasks[name] = processed

    return {
        "code": 0,
        "message": f"任务 '{name}' 创建成功" + ("并已热加载" if scheduler and trigger_type == "system" else ""),
        "data": {
            "name": name,
            "type": processed.get("type", "web"),
            "schedule": processed.get("schedule"),
            "trigger_type": trigger_type,
            "target_table": processed.get("target_table"),
        },
    }