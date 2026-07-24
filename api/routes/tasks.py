"""Task management endpoints."""

import time
import logging
from fastapi import APIRouter, Request, HTTPException
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


@router.get("")
async def list_tasks(request: Request):
    """List all tasks."""
    tasks = _load_tasks(request)
    result = []
    for t in tasks:
        result.append({
            "name": t.get("name"),
            "type": t.get("type"),
            "schedule": t.get("schedule"),
            "target_table": t.get("target_table"),
            "enabled": t.get("enabled", True),
        })
    return {"code": 0, "message": "success", "data": result}


@router.get("/{task_name}")
async def get_task(request: Request, task_name: str):
    """Get a single task config."""
    tasks = _load_tasks(request)
    for t in tasks:
        if t.get("name") == task_name:
            return {"code": 0, "message": "success", "data": t}
    raise HTTPException(status_code=404, detail=f"Task not found: {task_name}")


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

    task_id = target.get("_task_id", 0)
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