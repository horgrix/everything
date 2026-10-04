"""
Crawler engine: unified pipeline model.

All tasks converge into a single pipeline:
  context expansion (iterate) → data fetch (via SourceRegistry) →
  output expansion (outputs) → parse → clean → write
"""

import asyncio
import logging
import itertools
from .dedup import URLDedup
from .pipeline import DataPipeline, PipelineResult
from .template import URLTemplate
from .sources.base import SourceRegistry

logger = logging.getLogger(__name__)


class CrawlerEngine:
    """
    Crawler engine — orchestrates the full pipeline.

    Delegates data fetching to pluggable DataSource implementations
    registered in a SourceRegistry.  No more if-else routing.

    Usage:
        sources = SourceRegistry()
        sources.register("api", HttpSource())
        sources.register("web", HttpSource(browser_source=BrowserSource()))
        sources.register("sdk", SdkSource())
        sources.register("csv", FileSource())
        sources.register("excel", FileSource())
        sources.register("db", DbSource())

        engine = CrawlerEngine(sources)
        stats = await engine.run(task_config, db)
    """

    def __init__(self, sources: SourceRegistry = None, pipeline: DataPipeline = None):
        self._sources = sources or SourceRegistry()
        self._pipeline = pipeline or DataPipeline()
        self._url_dedup = URLDedup(cache_ttl_seconds=300)

    async def close(self) -> None:
        """关闭所有数据源持有的资源（如 HTTP 连接池）。"""
        await self._sources.close()

    async def run(self, task_config: dict, db, url_context: dict = None) -> dict:
        """
        Execute a crawl task.

        Pipeline: iterate expand → fetch → outputs expand → process each output.

        Returns:
            {"new": N, "updated": N, "skipped": N, "error": str|None}
        """
        if url_context is None:
            url_context = {}

        # Inject global params into base context
        base_context = dict(url_context)
        params = task_config.get("params", {})
        if params:
            base_context.update(params)

        # Inject task_name into context
        base_context.setdefault("task_name", task_config.get("name", ""))

        contexts = self._build_iterate_contexts(task_config, base_context)
        total = PipelineResult()
        error_msg = None
        request_interval = float(task_config.get("request_interval", 0) or 0)

        # 批次节奏：每批顺序执行 request_batch.size 个请求，批间暂停 request_batch.pause 秒
        request_batch = task_config.get("request_batch") or {}
        request_batch_size = int(request_batch.get("size", 0) or 0)
        request_batch_pause = float(request_batch.get("pause", 0) or 0)

        # 批量上传：每个 output 累积清洗后的 rows，按 batch_size 分批 POST
        outputs = self._resolve_outputs(task_config)
        collected_rows = [[] for _ in outputs]

        for idx, ctx in enumerate(contexts):
            # 每个 iterate 请求之间的固定间隔（批内），用于限流 / 避免被限制访问
            if idx > 0 and request_interval > 0:
                logger.debug(
                    "Iterate request interval: waiting %.2fs", request_interval
                )
                await asyncio.sleep(request_interval)

            # 批间暂停：每 request_batch_size 个请求后暂停一次（第一批前不暂停）
            if (
                request_batch_size > 0
                and idx > 0
                and idx % request_batch_size == 0
                and request_batch_pause > 0
            ):
                logger.debug(
                    "Request batch pause: waiting %.2fs (after %d requests)",
                    request_batch_pause, idx,
                )
                await asyncio.sleep(request_batch_pause)

            # Resolve template variables in all context values before fetch
            ctx = self._resolve_all_templates(ctx)

            # 1. Fetch raw data via SourceRegistry
            try:
                raw_data = await self._fetch_data(task_config, ctx)
            except Exception as e:
                logger.error("[%d/%d] Fetch failed: %s", idx + 1, len(contexts), e)
                error_msg = str(e)
                continue

            if raw_data is None:
                continue

            # 2. Expand outputs + process each via pipeline（批量模式累积 rows）
            for oi, output_config in enumerate(outputs):
                total += self._pipeline.process(
                    raw_data, output_config, db, ctx,
                    collect_rows=collected_rows[oi],
                )
                # 攒够 batch_size 就 flush 一批（未配置 batch_size 时循环结束统一 flush）
                batch_size = self._get_batch_size(output_config)
                if batch_size > 0 and len(collected_rows[oi]) >= batch_size:
                    total += self._flush_output_api(
                        output_config, collected_rows[oi], base_context,
                    )

        # 3. 循环结束后 flush 每个 output 剩余的 rows
        for oi, output_config in enumerate(outputs):
            total += self._flush_output_api(
                output_config, collected_rows[oi], base_context,
            )

        return {
            "new": total.inserted,
            "updated": total.updated,
            "skipped": total.total - total.inserted - total.updated,
            "api_sent": total.api_sent,
            "api_failed": total.api_failed,
            "error": error_msg,
        }

    # ================================================================
    # Context expansion: iterate (Cartesian product of N variables)
    # ================================================================

    def _build_iterate_contexts(self, task_config: dict, base_context: dict) -> list[dict]:
        """
        If 'iterate' is configured, expand into a list of contexts;
        otherwise return a single context with resolved URL.

        Supports multi-variable Cartesian product:
          iterate:
            - var_name: "region"
              values: [global, CN]
            - var_name: "page"
              values: [1, 2]
        """
        raw_url = base_context.get("url") or task_config.get("url", "")
        iterate_config = task_config.get("iterate", {})

        if not iterate_config:
            ctx = dict(base_context)
            ctx["url"] = URLTemplate.resolve(raw_url, context=ctx)
            return [ctx]

        # Normalize to list format
        if isinstance(iterate_config, dict):
            iterate_config = [iterate_config]

        var_names = [item["var_name"] for item in iterate_config]
        values_lists = [item["values"] for item in iterate_config]

        contexts = []
        for combination in itertools.product(*values_lists):
            ctx = dict(base_context)
            for var_name, val in zip(var_names, combination):
                ctx[var_name] = str(val)
            ctx["url"] = URLTemplate.resolve(raw_url, context=ctx)
            contexts.append(ctx)

        logger.info("Iterate expansion: %s → %d contexts",
                     ", ".join(var_names), len(contexts))
        return contexts

    # ================================================================
    # Template resolution: recursively replace {var} in context values
    # ================================================================

    @staticmethod
    def _resolve_all_templates(ctx: dict) -> dict:
        """Walk all context values, resolving template variables in strings."""
        def _resolve_value(value):
            if isinstance(value, str):
                return URLTemplate.resolve(value, context=ctx)
            if isinstance(value, dict):
                return {k: _resolve_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_resolve_value(v) for v in value]
            return value

        return {k: _resolve_value(v) for k, v in ctx.items()}

    # ================================================================
    # Data fetch: delegated to SourceRegistry (no if-else)
    # ================================================================

    async def _fetch_data(self, task_config: dict, ctx: dict):
        """
        Route to the correct DataSource via SourceRegistry.

        For HTTP-based types ('api', 'web'), also applies URL dedup
        before delegating to the source.
        """
        task_type = task_config.get("type", "web")

        # URL dedup for HTTP types (SDK / file / db don't use URLs)
        if task_type in ("api", "web"):
            url = ctx.get("url") or task_config.get("url")
            if self._url_dedup.is_duplicate(url):
                logger.info("URL dedup skip: %s", url)
                return None
            logger.info("Request: %s %s", task_config.get("method", "GET"), url)

        source = self._sources.get(task_type)
        return await source.fetch(task_config, ctx)

    # ================================================================
    # Output expansion
    # ================================================================

    def _resolve_outputs(self, task_config: dict) -> list[dict]:
        """Return outputs config list (loader wraps single-output as [output])."""
        return task_config.get("outputs", [])

    # ================================================================
    # Batched target_api upload
    # ================================================================

    @staticmethod
    def _get_batch_size(output_config: dict) -> int:
        """读取 output.target_api.batch_size（正整数），未配置/非法返回 0。

        batch_size > 0 时按批 flush；= 0 时循环结束后统一 flush。
        """
        target_api = output_config.get("target_api") or {}
        raw = target_api.get("batch_size", 0)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _flush_output_api(
        self, output_config: dict, collected_rows: list, context: dict,
    ) -> PipelineResult:
        """flush 单个 output 累积的 rows 到 target_api，并清空缓冲。"""
        target_api = output_config.get("target_api")
        if not target_api or not collected_rows:
            return PipelineResult()
        result = self._pipeline.flush_api(target_api, collected_rows, context)
        collected_rows.clear()
        return result
