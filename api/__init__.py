"""
FastAPI 应用实例。

启动方式:
    python -m api                          # 纯 API 模式（默认端口 8000）
    python -m api --port 8080              # 指定端口
    python main.py --api                   # 同时启动 API + 调度器
"""

import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("api")


def create_app(config_dir: str = None, db_path: str = None) -> FastAPI:
    """创建 FastAPI 应用实例并挂载路由。"""

    # 从环境变量读取配置（uvicorn --factory 模式下无参数传入）
    if config_dir is None:
        config_dir = os.environ.get("API_CONFIG_DIR", "config/tasks")
    if db_path is None:
        db_path = os.environ.get("API_DB_PATH", "crawler.db")

    app = FastAPI(
        title="Crawler API",
        description="轻量级 Python 爬虫系统 HTTP API 接口",
        version="1.0.0",
    )

    # 将配置注入到 app.state，供路由使用
    app.state.config_dir = config_dir
    app.state.db_path = db_path

    # 懒加载数据库连接（避免导入时就连接）
    from storage.database import Database
    app.state.db = Database(db_path)
    app.state.db.init_system_tables()

    # Static files (Dashboard UI)
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="dashboard")

    # API routes
    from .routes.tasks import router as tasks_router
    from .routes.logs import router as logs_router
    from .routes.data import router as data_router
    from .routes.system import router as system_router
    app.include_router(tasks_router, prefix="/api/tasks", tags=["tasks"])
    app.include_router(logs_router, prefix="/api/logs", tags=["logs"])
    app.include_router(data_router, prefix="/api/data", tags=["data"])
    app.include_router(system_router, prefix="/api/system", tags=["system"])

    # 全局异常处理
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("未捕获异常: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"code": -1, "message": str(exc), "data": None},
        )

    return app