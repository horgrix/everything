"""
Data pipeline: parse → clean → filter → inject → write.

Extracted from CrawlerEngine so the engine only orchestrates
while the pipeline handles all data transformation.

Output is no longer hard-coded: the pipeline walks an
:class:`~crawler.sinks.base.SinkRegistry` and dispatches to every sink whose
``output`` key is present. Batching is owned by the sinks themselves (see
``crawler/sinks/``).
"""

from dataclasses import dataclass
import json  # noqa: F401  (kept so tests can patch crawler.pipeline.urllib)
import logging
import urllib.error  # noqa: F401
import urllib.request  # noqa: F401
from typing import Any

from .parser import Parser
from .cleaner import Cleaner
from .sinks import create_default_sink_registry
from .sinks.base import SinkRegistry, SinkResult
from .sinks.api_sink import ApiSink

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing one output target."""
    inserted: int = 0
    updated: int = 0
    total: int = 0
    api_sent: int = 0
    api_failed: int = 0

    def __add__(self, other: "PipelineResult") -> "PipelineResult":
        return PipelineResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            total=self.total + other.total,
            api_sent=self.api_sent + other.api_sent,
            api_failed=self.api_failed + other.api_failed,
        )

    def add_sink_result(self, sink_result: SinkResult) -> "PipelineResult":
        """Fold a SinkResult into this result (mutates and returns self)."""
        self.inserted += sink_result.inserted
        self.updated += sink_result.updated
        self.api_sent += sink_result.sent
        self.api_failed += sink_result.failed
        return self


class DataPipeline:
    """
    Stateless data transformation pipeline.

    Takes raw data from a DataSource and an output config,
    runs the full chain: parse → clean → filter → inject → sink dispatch.

    Usage:
        pipeline = DataPipeline(parser, cleaner, sinks)
        result = pipeline.process(raw_data, output_config, db, context)
    """

    def __init__(self, parser: Parser = None, cleaner: Cleaner = None,
                 sinks: SinkRegistry = None):
        self._parser = parser or Parser()
        self._cleaner = cleaner or Cleaner()
        self._sinks = sinks if sinks is not None else create_default_sink_registry()

    @property
    def sinks(self) -> SinkRegistry:
        return self._sinks

    # ================================================================
    # Sync entry point (used by tests / direct callers)
    # ================================================================

    def process(
        self,
        raw_data: Any,
        output_config: dict,
        db,
        context: dict,
        collect_rows: list | None = None,
    ) -> PipelineResult:
        """
        Process one output target against raw data (synchronous).

        Dispatches to every registered sink whose key is present, calling
        ``prepare`` then ``write``, and finally ``flush`` (immediate mode).

        Args:
            collect_rows: **Deprecated.** Legacy batch mode kept for backward
                compatibility: when a list is passed, cleaned rows for a
                ``target_api`` output are appended to it instead of being
                dispatched, so the caller can flush them later. The engine no
                longer uses this (sinks buffer internally).

        Returns:
            PipelineResult with inserted/updated/total/api_sent/api_failed.
        """
        result = PipelineResult()
        self._prepare_sinks(output_config, context, db)

        cleaned = self._transform(raw_data, output_config, context)
        if cleaned is None:
            return result

        result.total = len(cleaned)
        if not cleaned:
            return result

        for sink in self._sinks.all():
            if sink.key not in output_config:
                continue
            if collect_rows is not None and sink.key == "target_api":
                collect_rows.extend(cleaned)
                continue
            result.add_sink_result(sink.write(cleaned, output_config, context, db))
            if collect_rows is None:
                result.add_sink_result(sink.flush(output_config, context, db))

        return result

    # ================================================================
    # Async entry point (used by the engine)
    # ================================================================

    async def process_async(
        self,
        raw_data: Any,
        output_config: dict,
        db,
        context: dict,
    ) -> PipelineResult:
        """Async variant: prepare + write only; caller flushes at end of run."""
        result = PipelineResult()
        await self._prepare_sinks_async(output_config, context, db)

        cleaned = self._transform(raw_data, output_config, context)
        if cleaned is None:
            return result

        result.total = len(cleaned)
        if not cleaned:
            return result

        for sink in self._sinks.all():
            if sink.key not in output_config:
                continue
            sink_result = await sink.write_async(cleaned, output_config, context, db)
            result.add_sink_result(sink_result)

        return result

    # ================================================================
    # Flush: end-of-run, once per output per sink
    # ================================================================

    async def flush_outputs(self, outputs: list[dict], context: dict, db) -> PipelineResult:
        """Flush every sink hit by ``outputs`` (end of run)."""
        result = PipelineResult()
        for output_config in outputs:
            for sink in self._sinks.all():
                if sink.key not in output_config:
                    continue
                sink_result = await sink.flush_async(output_config, context, db)
                result.add_sink_result(sink_result)
        return result

    def flush_outputs_sync(self, outputs: list[dict], context: dict, db) -> PipelineResult:
        """Synchronous variant of :meth:`flush_outputs`."""
        result = PipelineResult()
        for output_config in outputs:
            for sink in self._sinks.all():
                if sink.key not in output_config:
                    continue
                result.add_sink_result(sink.flush(output_config, context, db))
        return result

    def flush_api(
        self,
        target_api_config: dict,
        rows: list[dict],
        context: dict,
    ) -> PipelineResult:
        """POST accumulated rows to ``target_api`` in a single request.

        Backward-compatible helper (the ApiSink now owns batching).
        """
        if not rows:
            return PipelineResult()
        if ApiSink._post_to_api(target_api_config, rows, context):
            return PipelineResult(api_sent=len(rows))
        return PipelineResult(api_failed=len(rows))

    # Kept for backward compatibility with older callers/tests.
    def _post_to_api(self, target_api_config: dict, rows: list[dict], context: dict) -> bool:
        return ApiSink._post_to_api(target_api_config, rows, context)

    def close_sinks(self) -> None:
        """Release resources held by sinks (DB connections, ...)."""
        self._sinks.close()

    # ================================================================
    # Internal
    # ================================================================

    def _hit_sinks(self, output_config: dict):
        return [s for s in self._sinks.all() if s.key in output_config]

    def _prepare_sinks(self, output_config: dict, context: dict, db) -> None:
        for sink in self._hit_sinks(output_config):
            sink.prepare(output_config, context, db)

    async def _prepare_sinks_async(self, output_config: dict, context: dict, db) -> None:
        for sink in self._hit_sinks(output_config):
            await sink.prepare_async(output_config, context, db)

    def _transform(self, raw_data: Any, output_config: dict, context: dict) -> list[dict] | None:
        """parse → clean → inject source_url. Returns None when no rows parsed."""
        parser_config = output_config.get("parser", {})

        element_selector_config = parser_config.get("element_selector", {})
        if element_selector_config:
            element_vars = self._parser.extract_element_vars(
                raw_data, element_selector_config
            )
            context.update(element_vars)

        parsed = self._parser.parse_rows(raw_data, parser_config, context=context)
        if not parsed:
            return None

        parser_fields = parser_config.get("fields", [])
        cleaned = self._cleaner.clean_batch(parsed, parser_fields)

        src_field_names = Cleaner.field_names(parser_fields)
        url = context.get("url", "")
        if "source_url" in src_field_names and url:
            for row in cleaned:
                if "source_url" not in row:
                    row["source_url"] = url

        return cleaned
