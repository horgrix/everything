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
from storage.database import Database
from scheduler.scheduler import CrawlScheduler


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


async def run_once(task_name: str, config_dir: str, db: Database):
    """执行一次指定任务后退出"""
    from task_manager.loader import TaskLoader
    from crawler.engine import CrawlerEngine

    loader = TaskLoader(config_dir, db)
    tasks = loader.load_all()

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
        return

    engine = CrawlerEngine()
    task_id = target["_task_id"]
    log_id = db.start_crawl_log(task_id)

    import time
    start = time.time()
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

    print(f"\n任务 '{task_name}' 执行结果:")
    print(f"  新增: {stats.get('new', 0)}")
    print(f"  更新: {stats.get('updated', 0)}")
    print(f"  跳过: {stats.get('skipped', 0)}")
    print(f"  耗时: {duration_ms / 1000:.1f}s")
    if stats.get("error"):
        print(f"  错误: {stats['error']}")


async def _async_main(args):
    """异步主函数：调度器必须在事件循环内部启动。"""
    logger = logging.getLogger("main")

    # 初始化数据库
    db_path = args.db
    db = Database(db_path)
    db.init_system_tables()
    logger.info("数据库已就绪: %s", os.path.abspath(db_path))

    # 确保配置目录存在
    config_dir = args.config
    os.makedirs(config_dir, exist_ok=True)

    # --run-once 模式
    if args.run_once:
        await run_once(args.run_once, config_dir, db)
        db.close()
        return

    # 正常调度模式
    scheduler = CrawlScheduler(config_dir=config_dir, db=db)

    # 使用 asyncio.Event 实现优雅关闭
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler，回退到 signal.signal
            signal.signal(sig, lambda s, f: stop_event.set())

    # 启动调度器（此时事件循环已运行）
    scheduler.start()

    # --api 模式：同时启动 HTTP API 服务
    api_server = None
    if args.api:
        import uvicorn
        from api import create_app

        api_config = uvicorn.Config(
            create_app(config_dir=config_dir, db_path=db_path),
            host="0.0.0.0",
            port=args.api_port,
            log_level=args.log_level.lower(),
        )
        api_server = uvicorn.Server(api_config)
        logger.info("API 服务启动在 http://0.0.0.0:%d", args.api_port)
        # 在后台启动 API（与调度器共享事件循环）
        api_task = asyncio.create_task(api_server.serve())

    try:
        await stop_event.wait()
    finally:
        logger.info("收到退出信号，正在关闭...")
        scheduler.shutdown()
        if api_server:
            api_server.should_exit = True
            await api_task
        db.close()
        logger.info("系统已退出")


def main():
    args = parse_args()
    setup_logging(args.log_level)
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()