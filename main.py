"""
爬虫系统入口。

启动方式:
    python main.py                          # 使用默认配置
    python main.py --config config/tasks    # 指定配置目录
    python main.py --db crawler.db          # 指定数据库路径
    python main.py --log-level DEBUG        # 设置日志级别
"""

import os
import argparse
import asyncio
import signal
import logging
from app import create_app


def setup_logging(level: str = "INFO"):
    """配置日志格式"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args():
    parser = argparse.ArgumentParser(description="轻量级 Python 爬虫系统")
    parser.add_argument(
        "--config", "-c",
        default="config/tasks",
        help="任务配置文件目录 (默认: config/tasks)",
    )
    parser.add_argument(
        "--db", "-d",
        default="crawler.db",
        help="SQLite 数据库路径 (默认: crawler.db)",
    )
    parser.add_argument(
        "--log-level", "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )
    parser.add_argument(
        "--run-once", "-r",
        default=None,
        help="仅运行指定任务一次（按任务名）后退出",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        default=False,
        help="同时启动 HTTP API 服务",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=8000,
        help="API 服务端口 (默认: 8000)",
    )
    return parser.parse_args()


async def run_once(task_name: str, config_dir: str, db_path: str):
    """Execute a task once, then exit."""
    app = create_app(config_dir=config_dir, db_path=db_path)
    tasks = app.loader.load_all()

    target = None
    for t in tasks:
        if t["name"] == task_name:
            target = t
            break

    if target is None:
        print(f"未找到任务: {task_name}")
        print("可用任务:")
        for t in tasks:
            print(f"  - {t['name']}")
        app.db.close()
        return

    task_id = target["_task_id"]
    log_id = app.db.start_crawl_log(task_id)

    import time
    start = time.time()
    stats = await app.engine.run(target, app.db)
    duration_ms = int((time.time() - start) * 1000)

    if stats.get("error"):
        app.db.fail_crawl_log(log_id, stats["error"], duration_ms)
    else:
        app.db.finish_crawl_log(
            log_id,
            records_new=stats.get("new", 0),
            records_updated=stats.get("updated", 0),
            records_skipped=stats.get("skipped", 0),
            duration_ms=duration_ms,
        )

    print(f"\n任务 '{task_name}' 执行结果:")
    print(f"  新增: {stats.get('new', 0)}")
    print(f"  更新: {stats.get('updated', 0)}")
    print(f"  跳过: {stats.get('skipped', 0)}")
    print(f"  耗时: {duration_ms / 1000:.1f}s")
    if stats.get("error"):
        print(f"  错误: {stats['error']}")

    app.db.close()


async def _async_main(args):
    """Async entry point."""
    logger = logging.getLogger("main")

    config_dir = args.config
    db_path = args.db

    # --run-once mode: single task, no scheduler
    if args.run_once:
        await run_once(args.run_once, config_dir, db_path)
        return

    # Normal mode: assemble app + start scheduler
    app = create_app(config_dir=config_dir, db_path=db_path)

    # Graceful shutdown
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: stop_event.set())

    app.scheduler.start()

    # --api mode: also start HTTP API
    api_server = None
    if args.api:
        import uvicorn
        from api import create_app as create_api_app

        api_config = uvicorn.Config(
            create_api_app(config_dir=config_dir, db_path=db_path, scheduler=app.scheduler),
            host="0.0.0.0",
            port=args.api_port,
            log_level=args.log_level.lower(),
        )
        api_server = uvicorn.Server(api_config)
        logger.info("API 服务启动在 http://0.0.0.0:%d", args.api_port)
        api_task = asyncio.create_task(api_server.serve())

    try:
        await stop_event.wait()
    finally:
        logger.info("收到退出信号，正在关闭...")
        app.scheduler.shutdown()
        if api_server:
            api_server.should_exit = True
            await api_task
        app.db.close()
        logger.info("系统已退出")


def main():
    args = parse_args()
    setup_logging(args.log_level)
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()