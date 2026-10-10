"""Task management endpoints."""

import time
import logging
import yaml
import os
from pathlib import Path
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


def _task_filepath(config_dir: str, task_name: str,
                   trigger_type: str = "system", group: str = "default") -> str:
    """根据 group + trigger_type 确定 YAML 文件存放路径。

    - system → config/tasks/<group>/<name>.yaml
    - user   → config/tasks/<group>/manual/<name>.yaml
    """
    sub = "manual" if trigger_type == "user" else ""
    return os.path.join(config_dir, group, sub, f"{task_name}.yaml")


def _find_task_file(config_dir: str, task_name: str):
    """在所有分组目录根 + manual/ 下查找任务文件。

    Returns:
        (group, trigger_type, filepath) 或 (None, None, None)。
    """
    base = Path(config_dir)
    if not base.is_dir():
        return None, None, None
    for group_dir in sorted(base.iterdir()):
        if not group_dir.is_dir():
            continue
        root = group_dir / f"{task_name}.yaml"
        if root.exists():
            return group_dir.name, "system", str(root)
        manual = group_dir / "manual" / f"{task_name}.yaml"
        if manual.exists():
            return group_dir.name, "user", str(manual)
    return None, None, None


@router.get("")
async def list_tasks(request: Request):
    """List all tasks."""
    tasks = _load_tasks(request)
    result = []
    for t in tasks:
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
            "group": t.get("_group") or t.get("group") or "",
            "depends_on": t.get("depends_on") or [],
            "enabled": t.get("enabled", True),
        })
    return {"code": 0, "message": "success", "data": result}


@router.get("/groups")
async def list_groups(request: Request):
    """列出所有分组及其任务数量。"""
    tasks = _load_tasks(request)
    groups: dict[str, dict] = {}
    for t in tasks:
        group = t.get("_group") or t.get("group") or ""
        entry = groups.setdefault(
            group, {"group": group, "system": 0, "user": 0, "total": 0}
        )
        if t.get("_trigger_type") == "system":
            entry["system"] += 1
        else:
            entry["user"] += 1
        entry["total"] += 1
    return {"code": 0, "message": "success",
            "data": sorted(groups.values(), key=lambda g: g["group"])}


@router.get("/{task_name}")
async def get_task(request: Request, task_name: str):
    """Get a single task config, including raw config_yaml from DB."""
    db = _get_db(request)
    row = db.conn.execute(
        "SELECT config_yaml FROM crawl_tasks WHERE task_name = ?", (task_name,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")

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

    task_row = db.conn.execute(
        "SELECT id FROM crawl_tasks WHERE task_name = ?", (task_name,)
    ).fetchone()
    if task_row is None:
        from task_manager.loader import TaskLoader
        loader = TaskLoader(_get_config_dir(request), db)
        target = loader._register_task(
            target,
            target.get("_trigger_type", "system"),
            None,
            target.get("_group", ""),
        )
    task_id = task_row["id"] if task_row else target.get("_task_id", 0)
    log_id = db.start_crawl_log(task_id)

    start = time.time()
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler and scheduler._engine:
        engine = scheduler._engine
    else:
        from crawler.sources import create_default_registry
        engine = CrawlerEngine(sources=create_default_registry())

    try:
        stats = await engine.run(target, db)
        stats.pop("raw_data", None)  # 不外泄大 body 到 HTTP 响应
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
    group: str = ""


@router.delete("/{task_name}")
async def delete_task(request: Request, task_name: str):
    """
    删除任务：从调度器移除 + 删除 YAML 文件 + 从数据库删除。
    """
    config_dir = _get_config_dir(request)
    db = _get_db(request)

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        try:
            scheduler._scheduler.remove_job(task_name)
        except Exception:
            pass

    # 在组目录根 + manual 里找文件删除
    _, _, fp = _find_task_file(config_dir, task_name)
    deleted_files = 0
    if fp and os.path.exists(fp):
        os.remove(fp)
        deleted_files = 1

    db.conn.execute("DELETE FROM crawl_tasks WHERE task_name = ?", (task_name,))
    db.conn.commit()

    return {
        "code": 0,
        "message": f"任务 '{task_name}' 已删除",
        "data": {"name": task_name, "files_deleted": deleted_files},
    }


@router.put("/{task_name}")
async def update_task(request: Request, task_name: str, body: CreateTaskRequest):
    """
    更新任务配置：覆写 YAML 文件 + 重新注册到数据库 + 热重载到调度器。
    """
    from api.deps import parse_task_yaml, validate_trigger_type, validate_group

    task_config = parse_task_yaml(body.config_yaml, task_name)
    config_dir = _get_config_dir(request)
    db = _get_db(request)
    trigger_type = validate_trigger_type(task_config)

    # 保留已有 group（除非请求显式指定新 group）
    group = validate_group(body.group) if body.group.strip() else ""
    if not group:
        row = db.conn.execute(
            "SELECT \"group\" FROM crawl_tasks WHERE task_name = ?", (task_name,)
        ).fetchone()
        group = (row["group"] if row and row["group"] else "") or "default"

    # 删除旧文件（若 group/trigger_type 变化）
    _, _, old_fp = _find_task_file(config_dir, task_name)
    filepath = _task_filepath(config_dir, task_name, trigger_type, group)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(task_config, f, allow_unicode=True, default_flow_style=False)
    if old_fp and os.path.abspath(old_fp) != os.path.abspath(filepath) and os.path.exists(old_fp):
        os.remove(old_fp)

    from task_manager.loader import TaskLoader
    loader = TaskLoader(config_dir, db)
    processed = loader._register_task(task_config, trigger_type, filepath, group)

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
            "group": group,
            "target_table": processed.get("target_table"),
        },
    }


@router.post("")
async def create_task(request: Request, body: CreateTaskRequest):
    """
    创建新任务：写入 YAML 文件 + 注册到数据库 + 热加载到调度器（仅 system）。
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="任务名称不能为空")

    from api.deps import parse_task_yaml, validate_trigger_type, validate_group

    task_config = parse_task_yaml(body.config_yaml, name)
    trigger_type = validate_trigger_type(task_config)
    group = validate_group(body.group)

    config_dir = _get_config_dir(request)
    db = _get_db(request)

    filepath = _task_filepath(config_dir, name, trigger_type, group)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(task_config, f, allow_unicode=True, default_flow_style=False)

    from task_manager.loader import TaskLoader
    loader = TaskLoader(config_dir, db)
    processed = loader._register_task(task_config, trigger_type, filepath, group)

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
            "group": group,
            "target_table": processed.get("target_table"),
        },
    }


@router.post("/groups/{group}/trigger")
async def trigger_group(request: Request, group: str):
    """触发某分组下所有根任务（depends_on 为空），级联触发下游。"""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=400, detail="调度器未启动，无法组级触发")
    await scheduler.trigger_group(group)
    return {"code": 0, "message": f"分组 '{group}' 触发完成", "data": {"group": group}}


def _set_group_enabled(request: Request, group: str, enabled: bool):
    """组级批量启停：更新 YAML enabled 字段 + DB 列 + 调度器热更新。"""
    tasks = _load_tasks(request)
    group_tasks = [
        t for t in tasks
        if (t.get("_group") or t.get("group") or "") == group
    ]
    if not group_tasks:
        raise HTTPException(status_code=404, detail=f"分组 '{group}' 无任务")

    config_dir = _get_config_dir(request)
    db = _get_db(request)

    for t in group_tasks:
        name = t.get("name")
        _, _, fp = _find_task_file(config_dir, name)
        if fp and os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                data["enabled"] = enabled
                with open(fp, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        db.conn.execute(
            "UPDATE crawl_tasks SET enabled=? WHERE task_name=?",
            (1 if enabled else 0, name),
        )
    db.conn.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        loader = TaskLoader(config_dir, db)
        fresh = {t["name"]: t for t in loader.load_all()}
        for name in [t.get("name") for t in group_tasks]:
            try:
                scheduler._scheduler.remove_job(name)
            except Exception:
                pass
            scheduler._tasks.pop(name, None)
            # 清理依赖反向索引中该任务的残留引用
            for upstream in list(scheduler._dependents.keys()):
                scheduler._dependents[upstream] = [
                    d for d in scheduler._dependents[upstream] if d.get("name") != name
                ]
            if name in fresh and fresh[name].get("_trigger_type") == "system":
                scheduler._register_job(fresh[name])

    return {
        "code": 0,
        "message": f"分组 '{group}' 已{'启用' if enabled else '禁用'} {len(group_tasks)} 个任务",
        "data": {"group": group, "enabled": enabled, "count": len(group_tasks)},
    }


@router.post("/groups/{group}/enable")
async def enable_group(request: Request, group: str):
    """启用分组下所有任务。"""
    return _set_group_enabled(request, group, True)


@router.post("/groups/{group}/disable")
async def disable_group(request: Request, group: str):
    """禁用分组下所有任务。"""
    return _set_group_enabled(request, group, False)
