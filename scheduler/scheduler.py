"""
任务调度模块：基于 APScheduler 的定时任务调度中心。

负责：
  - 加载所有 YAML 任务配置
  - 按 cron 表达式注册定时任务
  - 每次任务执行前后记录运行日志
  - 汇总统计结果
"""

import asyncio
import time
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
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

    def __init__(self, config_dir: str, db, engine=None, loader=None):
        self.config_dir = config_dir
        self.db = db
        self._scheduler = AsyncIOScheduler()
        self._engine = engine  # injected by create_app()
        self._loader = loader  # injected by create_app()
        self._tasks: dict[str, dict] = {}  # task_name -> task_config

        # 任务依赖（depends_on）运行时状态
        self._dependents: dict[str, list[dict]] = {}   # 上游名 -> 下游 task_config 列表
        self._last_success: dict[str, float] = {}       # 任务名 -> 最近成功完成时间(epoch 秒)
        self._last_triggered: dict[str, float] = {}     # 任务名 -> 最近一次触发运行时间(epoch 秒)
        self._running: set[str] = set()                 # 正在运行的任务名（防重入）

    def start(self):
        """启动调度器：加载任务 + 注册定时器 + 开始运行"""
        logger.info("=" * 50)
        logger.info("爬虫调度中心启动")
        logger.info("=" * 50)

        # 1. 加载所有 YAML 任务配置
        loader = self._loader or TaskLoader(self.config_dir, self.db)
        tasks = loader.load_all()

        if not tasks:
            logger.warning("未找到任何任务配置，调度器空闲运行")

        # 2. 仅注册 system 类型任务到 APScheduler（user 类型仅供手动触发）
        for task_config in tasks:
            if task_config.get("_trigger_type", "system") != "system":
                logger.info("跳过 user 类型任务: %s", task_config.get("name"))
                continue
            self._register_job(task_config)

        # 3. 校验依赖引用完整性
        self._validate_dependencies()

        # 4. 启动
        self._scheduler.start()
        logger.info("调度器已启动，共 %d 个任务", len(self._tasks))

        # 4. 打印任务清单
        for name, task in self._tasks.items():
            logger.info("  [%s] %s → %s", task.get("type", "web"), name, task["schedule"])

    def shutdown(self):
        """关闭调度器"""
        self._scheduler.shutdown(wait=False)
        logger.info("调度器已关闭")

    def add_job(self, task_config: dict):
        """
        热加载：动态注册一个新任务到调度器。

        流程：写入 YAML 文件 → 注册到数据库（建表） → 注册到 APScheduler。
        如果任务已存在则替换。
        """
        name = task_config.get("name")
        if not name:
            logger.error("add_job 失败: 缺少 name 字段")
            return None

        # 1. 注册到数据库 + 建表
        from task_manager.loader import TaskLoader
        loader = TaskLoader(self.config_dir, self.db)
        processed = loader._register_task(task_config)
        if processed is None:
            logger.error("add_job 失败: 任务注册到数据库失败")
            return None

        # 2. 同时写入 YAML 文件（持久化）
        import yaml
        import os
        filepath = os.path.join(self.config_dir, f"{name}.yaml")
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(task_config, f, allow_unicode=True, default_flow_style=False)
        logger.info("任务配置已写入: %s", filepath)

        # 3. 注册到 APScheduler
        self._register_job(processed)
        self._tasks[name] = processed
        logger.info("任务 '%s' 已热加载到调度器", name)
        return processed

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

        # 建立依赖反向索引（上游名 -> 下游任务列表）
        for upstream in (task_config.get("depends_on") or []):
            self._dependents.setdefault(upstream, []).append(task_config)

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

    async def _execute_task_wrapper(self, task_config: dict):
        """
        APScheduler 定时任务入口。所有触发（cron 兜底 / 上游完成事件）最终
        都汇聚到 _run_task，统一做防重入与依赖就绪检查。
        """
        await self._run_task(task_config)

    async def _run_task(self, task_config: dict) -> None:
        """
        运行单个任务（含依赖门控）。

        触发来源有两个：
          1. 自身 cron 定时（兜底，见 _register_job）
          2. 上游任务成功完成事件（见 _fire_dependents）
        两个来源共用同一套就绪判断，避免重复执行与脏数据。
        """
        name = task_config.get("name", "unknown")

        # 防重入：同一任务同时只跑一个实例
        if name in self._running:
            logger.warning("任务 '%s' 正在运行，跳过本次触发", name)
            return

        # 依赖就绪检查：depends_on 列出的上游必须全部「最近成功完成时间」
        # 晚于本任务上一次触发时间，才允许执行（首次触发时只要上游有成功记录即可）
        depends_on = task_config.get("depends_on") or []
        if not self._deps_ready(depends_on, name):
            logger.info(
                "任务 '%s' 依赖未满足（depends_on=%s），跳过本轮",
                name, depends_on,
            )
            return

        self._running.add(name)
        self._last_triggered[name] = time.time()
        try:
            stats = await self._execute(task_config)
            if stats is not None and not stats.get("error"):
                self._last_success[name] = time.time()
                await self._fire_dependents(name)
        finally:
            self._running.discard(name)

    async def _execute(self, task_config: dict) -> dict:
        """执行单个任务并记录运行日志，返回统计结果（含 error 字段）。"""
        name = task_config.get("name", "unknown")
        task_id = task_config.get("_task_id", 0)
        start_time = time.time()

        logger.info(">>> 开始执行任务: %s", name)

        # 创建运行日志（事件循环线程，SQLite 安全）
        log_id = self.db.start_crawl_log(task_id)

        try:
            stats = await self._engine.run(task_config, self.db)
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
            return stats

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self.db.fail_crawl_log(log_id, str(e), duration_ms)
            logger.error("任务 '%s' 执行失败: %s", name, e)
            return {"error": str(e)}

    def _deps_ready(self, depends_on: list, name: str) -> bool:
        """
        依赖就绪判断（AND 语义）：
        每个上游都必须有过成功记录，且其「最近成功完成时间」严格晚于本任务
        上一次触发时间。这样每轮上游完成会触发一次下游，而 cron 兜底到点时
        若本轮上游已通过事件触发过下游，则不会重复执行。
        """
        if not depends_on:
            return True
        last_triggered = self._last_triggered.get(name)
        for upstream in depends_on:
            last_success = self._last_success.get(upstream)
            if last_success is None:
                return False
            if last_triggered is not None and last_success <= last_triggered:
                return False
        return True

    async def _fire_dependents(self, upstream: str) -> None:
        """
        上游任务成功完成后，触发其直接下游中已满足依赖的任务。
        多个下游并行执行；下游自身若有更深依赖，会在其 _run_task 内继续递归。
        """
        downstreams = self._dependents.get(upstream, [])
        if not downstreams:
            return
        ready = [
            d for d in downstreams
            if self._deps_ready(d.get("depends_on") or [], d.get("name", "unknown"))
        ]
        if ready:
            await asyncio.gather(*(self._run_task(d) for d in ready))

    def _validate_dependencies(self) -> None:
        """校验 depends_on：上游存在性 + 循环依赖检测。"""
        # 1. 上游存在性
        for name, task_config in self._tasks.items():
            for upstream in (task_config.get("depends_on") or []):
                if upstream not in self._tasks:
                    logger.warning(
                        "任务 '%s' 依赖的上游 '%s' 未注册，该依赖将永远无法满足",
                        name, upstream,
                    )

        # 2. 循环依赖检测（DFS 三色标记）
        color = {n: 0 for n in self._tasks}  # 0=未访问 1=访问中 2=已完成

        def _has_cycle(node: str) -> bool:
            color[node] = 1
            for upstream in (self._tasks[node].get("depends_on") or []):
                if upstream not in color:
                    continue
                if color[upstream] == 1:
                    return True
                if color[upstream] == 0 and _has_cycle(upstream):
                    return True
            color[node] = 2
            return False

        for name in self._tasks:
            if color[name] == 0 and _has_cycle(name):
                logger.error(
                    "检测到任务依赖存在环，环上任务将无法执行，请检查 depends_on 配置"
                )
                break