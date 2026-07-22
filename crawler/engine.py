"""
爬虫引擎：统一流水线模型。

所有任务收敛为一条流水线：
  上下文展开（iterate） → 数据获取（SDK/HTTP） → 输出展开（outputs） → 解析清洗写入
"""

import logging
from .dedup import URLDedup
from .fetcher import Fetcher
from .parser import Parser
from .cleaner import Cleaner
from .anti_spider import AntiSpider
from .sdk_provider import SDKProvider
from .template import URLTemplate
from .fetcher_browser import BrowserFetcher
from .file_reader import FileReader

logger = logging.getLogger(__name__)


class CrawlerEngine:
    """爬虫引擎，协调请求/解析/清洗/存储全流程"""

    def __init__(self):
        self._url_dedup = URLDedup(cache_ttl_seconds=300)
        self._parser = Parser()
        self._cleaner = Cleaner()
        self._file_reader = FileReader()

    async def run(self, task_config: dict, db, url_context: dict = None) -> dict:
        """
        执行一次爬取任务。

        流程：iterate 展开 → fetch → outputs 展开 → 解析清洗写入

        返回:
            {"new": N, "updated": N, "skipped": N, "error": str|None}
        """
        if url_context is None:
            url_context = {}

        contexts = self._build_iterate_contexts(task_config, url_context)
        stats = {"new": 0, "updated": 0, "skipped": 0, "error": None}

        for idx, ctx in enumerate(contexts):
            # 1. 获取原始数据
            try:
                raw_data = await self._fetch_data(task_config, ctx)
            except Exception as e:
                logger.error("[%d/%d] 数据获取失败: %s", idx + 1, len(contexts), e)
                stats["error"] = str(e)
                continue

            if raw_data is None:
                continue

            # 2. 展开输出目标
            outputs = self._resolve_outputs(task_config)

            # 3. 对每个输出目标执行 解析→清洗→写入
            for output_config in outputs:
                r = self._process_output(raw_data, output_config, db, ctx)
                stats["new"] += r["inserted"]
                stats["updated"] += r["updated"]
                stats["skipped"] += r["total"] - r["inserted"] - r["updated"]

        return stats

    # ================================================================
    # 上下文展开：iterate 横切逻辑
    # ================================================================

    def _build_iterate_contexts(self, task_config: dict, url_context: dict) -> list[dict]:
        """
        如果配置了 iterate，按 values 展开为一组上下文；否则按主配置构建单个上下文。

        每个 context 包含解析后的 url 和注入的变量。
        """
        iterate_config = task_config.get("iterate", {})
        raw_url = url_context.get("url") or task_config.get("url", "")

        if not iterate_config:
            # 无 iterate：按主配置构建单个上下文
            ctx = {**url_context}
            ctx["url"] = URLTemplate.resolve(raw_url, context=ctx)
            return [ctx]

        var_name = iterate_config["var_name"]
        values = iterate_config["values"]

        contexts = []
        for val in values:
            ctx = {**url_context, var_name: str(val)}
            ctx["url"] = URLTemplate.resolve(raw_url, context=ctx)
            contexts.append(ctx)

        logger.info("iterate 展开: %s 共 %d 个上下文", var_name, len(contexts))
        return contexts

    # ================================================================
    # 数据获取：SDK / HTTP 路由
    # ================================================================

    async def _fetch_data(self, task_config: dict, ctx: dict):
        """
        根据 task type 获取原始数据。

        type=sdk → SDKProvider.call；否则 → HTTP/browser fetch。
        """
        task_type = task_config.get("type", "web")

        if task_type == "sdk":
            provider = SDKProvider()
            return provider.call(task_config.get("provider", {}))

        if task_type in ("csv", "excel"):
            return self._file_reader.read(task_config.get("file", {}))

        # HTTP / browser 请求
        url = ctx.get("url") or task_config.get("url")
        method = task_config.get("method", "GET")

        if self._url_dedup.is_duplicate(url):
            logger.info("URL 去重跳过: %s", url)
            return None

        logger.info("请求: %s %s", method, url)
        return await self._fetch(task_config, url, method)

    # ================================================================
    # 输出展开：outputs 横切逻辑
    # ================================================================

    def _resolve_outputs(self, task_config: dict) -> list[dict]:
        """
        如果配置了 outputs，返回 outputs 列表；否则将主配置包装为单元素列表。

        每个 output_config 包含: target_table, parser, table_schema（可选）
        """
        outputs = task_config.get("outputs")
        if outputs:
            return outputs

        # 单表模式：主配置即输出配置
        return [{
            "target_table": task_config["target_table"],
            "parser": task_config.get("parser", {}),
            "table_schema": task_config.get("table_schema", {}),
        }]

    # ================================================================
    # 核心流水线：解析 → 清洗 → 注入 → 写入（唯一路径）
    # ================================================================

    def _process_output(self, raw_data, output_config: dict, db, ctx: dict) -> dict:
        """
        对单次获取的原始数据，按输出配置完成：解析 → 清洗 → 注入 source_url → 批量写入。

        返回:
            {"inserted": N, "updated": N, "total": N}
        """
        table = output_config["target_table"]
        parser_config = output_config.get("parser", {})
        parser_fields = parser_config.get("fields", [])
        table_schema = output_config.get("table_schema", {})

        # 动态建表（如果有 table_schema 配置）
        if table_schema:
            db.ensure_business_table(table, table_schema.get("columns", []),
                                     table_schema.get("indexes", []))

        # 解析
        parsed = self._parser.parse_rows(raw_data, parser_config, context=ctx)
        if not parsed:
            return {"inserted": 0, "updated": 0, "total": 0}

        # 清洗并过滤
        cleaned = self._cleaner.clean_batch(parsed, parser_fields)

        # 注入 source_url（如果字段配置中声明了 source_url）
        src_field_names = Cleaner.field_names(parser_fields)
        url = ctx.get("url", "")
        if "source_url" in src_field_names and url:
            for row in cleaned:
                if "source_url" not in row:
                    row["source_url"] = url

        # 批量写入
        result = db.insert_business_records_batch(table, cleaned)
        return {"inserted": result["inserted"], "updated": result["updated"], "total": len(cleaned)}

    # ================================================================
    # HTTP 请求（保留原逻辑）
    # ================================================================

    async def _fetch(self, task_config: dict, url: str, method: str) -> str:
        """统一请求入口：browser 模式 vs aiohttp 模式"""
        browser_config = task_config.get("browser", {})
        if browser_config:
            bf = BrowserFetcher(headless=browser_config.get("headless", True))
            try:
                return await bf.fetch(url, browser_config)
            finally:
                await bf.close()

        anti = AntiSpider(task_config.get("anti_spider", {}))
        retry = task_config.get("retry", {})
        encoding = task_config.get("encoding", 'utf-8')
        fetcher = Fetcher(
            max_retries=retry.get("max_attempts", 3),
            backoff_base=retry.get("backoff_base", 2.0),
        )
        async with anti:
            return await fetcher.fetch(url, method=method, encoding=encoding)