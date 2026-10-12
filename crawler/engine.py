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
from .sinks import create_default_sink_registry
from .sinks.base import SinkRegistry
from .sinks.api_sink import ApiSink
from .task_log import TaskLogHandler, resolve_log_config

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

    def __init__(self, sources: SourceRegistry = None, pipeline: DataPipeline = None,
                 log_dir: str = "logs", sinks: SinkRegistry = None):
        self._sources = sources or SourceRegistry()
        if pipeline is None:
            pipeline = DataPipeline(
                sinks=sinks if sinks is not None else create_default_sink_registry()
            )
        self._pipeline = pipeline
        self._url_dedup = URLDedup(cache_ttl_seconds=300)
        self._log_dir = log_dir

    async def close(self) -> None:
        """关闭所有数据源持有的资源（如 HTTP 连接池）。"""
        await self._sources.close()
        self._pipeline.close_sinks()

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

        # 输出目标：每个 output 的写入由 sink 自行缓冲并在收尾时 flush
        outputs = self._resolve_outputs(task_config)

        last_raw_data = None  # 最近一次成功 fetch 的 raw_data（供失败时写 error.log）

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

            last_raw_data = raw_data

            # 组级文件日志：成功后写 data.log（仅 logging.data 开启时）
            await self._log_data(task_config, raw_data)

            # 2. Expand outputs + process each via pipeline（sink 内部自行缓冲）
            for output_config in outputs:
                total += await self._pipeline.process_async(
                    raw_data, output_config, db, ctx,
                )

        # 3. 循环结束后 flush 每个 output 的 sink 缓冲
        total += await self._pipeline.flush_outputs(outputs, base_context, db)

        return {
            "new": total.inserted,
            "updated": total.updated,
            "skipped": total.total - total.inserted - total.updated,
            "api_sent": total.api_sent,
            "api_failed": total.api_failed,
            "error": error_msg,
            **({"raw_data": last_raw_data} if error_msg else {}),
        }

    async def _log_data(self, task_config: dict, raw_data) -> None:
        """组级文件日志：成功后写 data.log（开关开时才写）。"""
        group = task_config.get("_group") or task_config.get("group") or ""
        cfg = resolve_log_config(task_config)
        if not group or not cfg["enabled"] or not cfg["data"]:
            return
        handler = TaskLogHandler(group, self._log_dir)
        await handler.log_data(task_config.get("name", ""), raw_data)

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
    # Batched upload compatibility shim (batching now lives in ApiSink)
    # ================================================================

    @staticmethod
    def _get_batch_size(output_config: dict) -> int:
        """Deprecated: batching moved into ApiSink/CrawlerEngine sinks.

        Kept as a thin forwarder so existing callers/tests keep working.
        """
        return ApiSink.get_batch_size(output_config)
