"""
Output sink abstraction layer and registry.

Symmetric to ``crawler/sources/``: every sink claims exactly one
``output`` config key (``target_table`` / ``target_api`` / ``target_mysql``
/ ``target_file``).  ``DataPipeline`` walks the registry and dispatches to
every sink whose key appears in an output config, so one output can write
to several destinations at once.

Batching lives inside the sink: a sink owns its own buffer and decides when
to persist / upload (its own ``batch_size``), with ``flush()`` called by the
pipeline at the end of a run.

Sinks are intentionally synchronous (the whole transformation pipeline is
synchronous and SQLite is used from the event-loop thread).  Each sink
exposes an ``*_async`` facade so the engine can offload blocking work (e.g.
pymysql) with ``asyncio.to_thread`` without changing the sync interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import asyncio


@dataclass
class SinkResult:
    """Outcome of one sink operation.

    ``inserted`` / ``updated`` mirror the local-DB counters, ``sent`` /
    ``failed`` count records pushed to an external destination (API rows,
    file rows, MySQL rows).
    """

    inserted: int = 0
    updated: int = 0
    sent: int = 0
    failed: int = 0

    def __add__(self, other: "SinkResult") -> "SinkResult":
        return SinkResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            sent=self.sent + other.sent,
            failed=self.failed + other.failed,
        )


class OutputSink(ABC):
    """Abstract base for all output sinks.

    Each implementation claims one ``output`` key.  ``prepare`` is called
    before parsing (so the destination exists even for an empty result);
    ``write`` is called with the cleaned rows; ``flush`` persists anything
    buffered; ``close`` releases resources.
    """

    key: str = ""

    @abstractmethod
    def prepare(self, output_config: dict, context: dict, db) -> None:
        """Ensure the destination is ready (create table / directory / ...)."""
        ...

    @abstractmethod
    def write(self, rows: list[dict], output_config: dict, context: dict, db) -> SinkResult:
        """Write (or buffer) cleaned rows."""
        ...

    @abstractmethod
    def flush(self, output_config: dict, context: dict, db) -> SinkResult:
        """Persist any buffered rows."""
        ...

    def close(self) -> None:
        """Release resources. Override when the sink holds connections."""

    # ------------------------------------------------------------------
    # Async facades. By default they simply call the sync implementation on
    # the current thread (safe for SQLite, which must stay on its own
    # thread). Sinks doing blocking network/disk I/O override these with
    # ``asyncio.to_thread``.
    # ------------------------------------------------------------------

    async def prepare_async(self, output_config: dict, context: dict, db) -> None:
        self.prepare(output_config, context, db)

    async def write_async(
        self, rows: list[dict], output_config: dict, context: dict, db
    ) -> SinkResult:
        return self.write(rows, output_config, context, db)

    async def flush_async(self, output_config: dict, context: dict, db) -> SinkResult:
        return self.flush(output_config, context, db)

    async def close_async(self) -> None:
        self.close()


class SinkRegistry:
    """Maps output config keys to OutputSink instances."""

    def __init__(self):
        self._sinks: dict[str, OutputSink] = {}

    def register(self, key: str, sink: OutputSink) -> None:
        """Register a sink under an output config key."""
        self._sinks[key] = sink

    def get(self, key: str) -> OutputSink:
        """Look up a sink by key.

        Raises:
            ValueError: If no sink is registered for this key.
        """
        if key not in self._sinks:
            raise ValueError(
                f"Unknown sink key: '{key}'. Available: {sorted(self._sinks.keys())}"
            )
        return self._sinks[key]

    def keys(self) -> set[str]:
        """Return all registered output config keys."""
        return set(self._sinks.keys())

    def all(self) -> list[OutputSink]:
        """Return every registered sink (deduplicated by instance)."""
        seen: set[int] = set()
        sinks: list[OutputSink] = []
        for sink in self._sinks.values():
            if id(sink) in seen:
                continue
            seen.add(id(sink))
            sinks.append(sink)
        return sinks

    def close(self) -> None:
        """Close every unique sink instance."""
        seen: set[int] = set()
        for sink in self._sinks.values():
            if id(sink) in seen:
                continue
            seen.add(id(sink))
            sink.close()
