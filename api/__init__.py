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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("api")


def create_app(config_dir: str = None, db_path: str = None, scheduler=None) -> FastAPI:
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
    app.state.scheduler = scheduler  # 热加载用，纯 API 模式（--api 无调度器）为 None

    # 跨域访问（CORS）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # JSON 响应压缩：nginx 默认 gzip_types 只有 text/html，application/json 不会被压，
    # 而数据接口有一条 250KB+ 的映射查询，这里直接在应用层压（响应带 CORS 头也不影响）。
    # 浏览器默认会发 Accept-Encoding: gzip，压缩后自动带 Content-Encoding / Content-Length。
    # compresslevel 用 6：相比默认的 9，250KB JSON 只多 1.7KB，CPU 从 ~7.6ms 降到 ~3.8ms。
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    # 只读 SQL 查询结果缓存（默认 TTL 60s，写接口会清空）
    from .query_cache import QueryCache

    app.state.query_cache = QueryCache.from_env()

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

    # 全局异常处理（仅捕获非 HTTP 异常，避免干扰 StaticFiles 的 404）
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        if isinstance(exc, StarletteHTTPException):
            raise exc  # Let FastAPI handle HTTP exceptions normally
        logger.error("未捕获异常: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"code": -1, "message": str(exc), "data": None},
        )

    return app