"""
Crawler engine: unified pipeline model.

All tasks converge into a single pipeline:
  context expansion (iterate) → data fetch (via SourceRegistry) →
  output expansion (outputs) → parse → clean → write
"""

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

        for idx, ctx in enumerate(contexts):
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

            # 2. Expand outputs + process each via pipeline
            for output_config in self._resolve_outputs(task_config):
                total += self._pipeline.process(raw_data, output_config, db, ctx)

        return {
            "new": total.inserted,
            "updated": total.updated,
            "skipped": total.total - total.inserted - total.updated,
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
