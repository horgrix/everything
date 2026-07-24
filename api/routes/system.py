"""系统状态端点"""

import os
import logging
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db(request: Request):
    return request.app.state.db


@router.get("/status")
async def system_status(request: Request):
    """调度器运行状态、任务数、数据库大小。"""
    db = _get_db(request)

    # 任务数量
    task_count = db.conn.execute(
        "SELECT COUNT(*) as cnt FROM crawl_tasks"
    ).fetchone()["cnt"]

    enabled_count = db.conn.execute(
        "SELECT COUNT(*) as cnt FROM crawl_tasks WHERE enabled=1"
    ).fetchone()["cnt"]

    # 业务表数量
    table_count = db.conn.execute(
        "SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'crawl_%' "
        "AND name NOT LIKE 'dedup_%'"
    ).fetchone()["cnt"]

    # 数据库文件大小
    db_path = request.app.state.db_path
    db_size_mb = 0
    if os.path.exists(db_path):
        db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)

    return {
        "code": 0,
        "message": "success",
        "data": {
            "tasks_total": task_count,
            "tasks_enabled": enabled_count,
            "business_tables": table_count,
            "db_size_mb": db_size_mb,
            "db_path": os.path.abspath(db_path),
        },
    }


@router.get("/dashboard")
async def dashboard_stats(request: Request):
    """Dashboard 聚合统计（执行趋势 + 状态分布）。"""
    db = _get_db(request)

    # 最近7天各状态执行数
    status_rows = db.conn.execute("""
        SELECT status, COUNT(*) as cnt
        FROM crawl_logs
        WHERE run_time >= datetime('now', '-7 days', 'localtime')
        GROUP BY status
    """).fetchall()
    status_map = {r["status"]: r["cnt"] for r in status_rows}
    status_series = [
        status_map.get("success", 0),
        status_map.get("running", 0),
        status_map.get("failed", 0),
    ]

    # 最近7天每日执行趋势
    trend_rows = db.conn.execute("""
        SELECT date(run_time) as day,
               COUNT(*) as exec_count,
               SUM(records_new) as new_total,
               SUM(records_updated) as upd_total
        FROM crawl_logs
        WHERE run_time >= datetime('now', '-7 days', 'localtime')
        GROUP BY date(run_time)
        ORDER BY day
    """).fetchall()
    trend_days = [r["day"] for r in trend_rows]
    trend_exec = [r["exec_count"] for r in trend_rows]
    trend_new = [r["new_total"] or 0 for r in trend_rows]
    trend_upd = [r["upd_total"] or 0 for r in trend_rows]

    return {
        "code": 0,
        "message": "success",
        "data": {
            "status_series": status_series,
            "status_labels": ["成功", "运行中", "失败"],
            "trend_days": trend_days,
            "trend_exec": trend_exec,
            "trend_new": trend_new,
            "trend_upd": trend_upd,
        },
    }


@router.get("/health")
async def health_check():
    """健康检查端点。"""
    return {
        "code": 0,
        "message": "success",
        "data": {"status": "healthy"},
    }
