"""
Output sink abstraction layer.

Each sink claims one ``output`` config key so the pipeline can route without
if-else chains:

* ``target_table`` -> :class:`SqliteSink`   (local SQLite)
* ``target_api``   -> :class:`ApiSink`      (remote HTTP)
* ``target_mysql`` -> :class:`MysqlSink`    (remote MySQL)
* ``target_file``  -> :class:`FileSink`     (CSV / JSONL / JSON / Excel)
"""

from .base import OutputSink, SinkRegistry, SinkResult
from .sqlite_sink import SqliteSink
from .api_sink import ApiSink
from .mysql_sink import MysqlSink
from .file_sink import FileSink

# Keys owned by the built-in sinks. Kept in sync with
# ``create_default_sink_registry`` (asserted in tests).
SINK_KEYS = frozenset({"target_table", "target_api", "target_mysql", "target_file"})


def create_default_sink_registry() -> SinkRegistry:
    """Build a SinkRegistry with all built-in output sinks."""
    registry = SinkRegistry()
    registry.register("target_table", SqliteSink())
    registry.register("target_api", ApiSink())
    registry.register("target_mysql", MysqlSink())
    registry.register("target_file", FileSink())
    return registry


__all__ = [
    "OutputSink",
    "SinkRegistry",
    "SinkResult",
    "SqliteSink",
    "ApiSink",
    "MysqlSink",
    "FileSink",
    "SINK_KEYS",
    "create_default_sink_registry",
]
