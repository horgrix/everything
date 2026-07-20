"""
任务调度模块：基于 APScheduler 的定时任务调度中心。

负责：
  - 加载所有 YAML 任务配置
  - 按 cron 表达式注册定时任务
  - 每次任务执行前后记录运行日志
  - 汇总统计结果
"""

import time
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from crawler.engine import CrawlerEngine
from task_manager.loader import TaskLoader

logger = logging.getLogger(__name__)


class CrawlScheduler:
    """
    爬虫任务调度中心。

    启动后自动扫描配置目录，注册所有启用任务到 APScheduler。

    使用方式:
        scheduler = CrawlScheduler(config_dir="config/tasks", db=database)
        scheduler.start()
        # 保持运行
        import asyncio
        asyncio.get_event_loop().run_forever()
    """

    def __init__(self, config_dir: str, db):
        self.config_dir = config_dir
        self.db = db
        self._scheduler = AsyncIOScheduler()
        self._engine = CrawlerEngine()
        self._tasks: dict[str, dict] = {}  # task_name -> task_config

    def start(self):
        """启动调度器：加载任务 + 注册定时器 + 开始运行"""
        logger.info("=" * 50)
        logger.info("爬虫调度中心启动")
        logger.info("=" * 50)

        # 1. 加载所有 YAML 任务配置
        loader = TaskLoader(self.config_dir, self.db)
        tasks = loader.load_all()

        if not tasks:
            logger.warning("未找到任何任务配置，调度器空闲运行")

        # 2. 逐个注册到 APScheduler
        for task_config in tasks:
            self._register_job(task_config)

        # 3. 启动
        self._scheduler.start()
        logger.info("调度器已启动，共 %d 个任务", len(self._tasks))

        # 4. 打印任务清单
        for name, task in self._tasks.items():
            logger.info("  [%s] %s → %s", task.get("type", "web"), name, task["schedule"])

    def shutdown(self):
        """关闭调度器"""
        self._scheduler.shutdown(wait=False)
        logger.info("调度器已关闭")

    def _register_job(self, task_config: dict):
        """
        将单个任务配置注册为 APScheduler 定时任务。
        """
        name = task_config["name"]
        schedule = task_config["schedule"]
        enabled = task_config.get("enabled", True)

        if not enabled:
            logger.info("任务 '%s' 已禁用，跳过注册", name)
            return

        # 存入任务表
        self._tasks[name] = task_config

        # 解析 cron 表达式并添加任务
        try:
            trigger = CronTrigger.from_crontab(schedule)
        except (ValueError, TypeError) as e:
            logger.error("任务 '%s' 的 cron 表达式无效: %s - %s", name, schedule, e)
            return

        self._scheduler.add_job(
            func=self._execute_task_wrapper,
            trigger=trigger,
            args=[task_config],
            id=name,
            name=name,
            replace_existing=True,
        )

        logger.info("已注册定时任务: %s (cron: %s)", name, schedule)

    def _execute_task_wrapper(self, task_config: dict):
        """
        APScheduler 不允许直接运行 async 函数，
        此方法作为同步包装器。
        """
        name = task_config.get("name", "unknown")
        task_id = task_config.get("_task_id", 0)

        logger.info(">>> 开始执行任务: %s", name)
        start_time = time.time()

        # 创建运行日志
        log_id = self.db.start_crawl_log(task_id)

        try:
            # 在事件循环中运行异步任务
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果调度器的事件循环已运行，创建新任务
                asyncio.ensure_future(self._execute_task(task_config, log_id, start_time))
            else:
                # 兼容性：如果还没事件循环
                loop.run_until_complete(self._execute_task(task_config, log_id, start_time))
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self.db.fail_crawl_log(log_id, str(e), duration_ms)
            logger.error("任务 '%s' 执行异常: %s", name, e)

    async def _execute_task(self, task_config: dict, log_id: int, start_time: float):
        """
        实际执行爬取任务的异步方法。
        """
        name = task_config.get("name", "unknown")

        try:
            stats = await self._engine.run(task_config, self.db)

            # 如果任务配置了需要处理多页/多URL逻辑（如API分页），
            # 可以在此处扩展，目前先处理单URL模式

            duration_ms = int((time.time() - start_time) * 1000)

            if stats.get("error"):
                self.db.fail_crawl_log(log_id, stats["error"], duration_ms)
                logger.warning("任务 '%s' 部分失败: %s", name, stats["error"])
            else:
                self.db.finish_crawl_log(
                    log_id,
                    records_new=stats.get("new", 0),
                    records_updated=stats.get("updated", 0),
                    records_skipped=stats.get("skipped", 0),
                    duration_ms=duration_ms,
                )
                logger.info(
                    "<<< 任务完成: %s (新增 %d, 更新 %d, 跳过 %d, 耗时 %.1fs)",
                    name,
                    stats.get("new", 0),
                    stats.get("updated", 0),
                    stats.get("skipped", 0),
                    duration_ms / 1000,
                )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self.db.fail_crawl_log(log_id, str(e), duration_ms)
            logger.error("任务 '%s' 执行失败: %s", name, e)