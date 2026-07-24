"""执行日志端点"""

import logging
from fastapi import APIRouter, Request, Query

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_db(request: Request):
    return request.app.state.db


@router.get("")
async def list_logs(
    request: Request,
    task_name: str = Query(None, description="按任务名过滤"),
    status: str = Query(None, description="按状态过滤: running / success / failed"),
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """
    查询任务执行历史日志。
    """
    db = _get_db(request)

    where = []
    params = []

    if task_name:
        # 先找 task_id
        row = db.conn.execute(
            "SELECT id FROM crawl_tasks WHERE task_name=?", (task_name,)
        ).fetchone()
        if row:
            where.append("l.task_id = ?")
            params.append(row["id"])
        else:
            return {"code": 0, "message": "success", "data": [], "total": 0}

    if status:
        where.append("l.status = ?")
        params.append(status)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    # 计数
    count_row = db.conn.execute(
        f"SELECT COUNT(*) as cnt FROM crawl_logs l {where_clause}", params
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    # 查询（联表取任务名）
    rows = db.conn.execute(
        f"""SELECT l.id, l.task_id, t.task_name, l.status,
                   l.records_new, l.records_updated, l.records_skipped,
                   l.error_msg, l.duration_ms, l.run_time
            FROM crawl_logs l
            LEFT JOIN crawl_tasks t ON l.task_id = t.id
            {where_clause}
            ORDER BY l.run_time DESC
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()

    result = [dict(r) for r in rows]
    return {"code": 0, "message": "success", "data": result, "total": total}


@router.get("/{log_id}")
async def get_log(request: Request, log_id: int):
    """获取单条执行日志详情。"""
    db = _get_db(request)
    row = db.conn.execute(
        """SELECT l.*, t.task_name
           FROM crawl_logs l
           LEFT JOIN crawl_tasks t ON l.task_id = t.id
           WHERE l.id = ?""",
        (log_id,),
    ).fetchone()

    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"日志不存在: {log_id}")

    return {"code": 0, "message": "success", "data": dict(row)}