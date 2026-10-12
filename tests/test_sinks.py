"""Tests for the OutputSink abstraction, registry, and pipeline dispatch."""

import pytest

from crawler.pipeline import DataPipeline
from crawler.parser import Parser
from crawler.cleaner import Cleaner
from crawler.sinks import SINK_KEYS, create_default_sink_registry
from crawler.sinks.base import OutputSink, SinkRegistry, SinkResult
from crawler.sinks.sqlite_sink import SqliteSink
from task_manager.loader import TaskLoader


class _RecordingSink(OutputSink):
    """Minimal sink capturing the call order for dispatch assertions."""

    def __init__(self, key):
        self.key = key
        self.calls = []

    def prepare(self, output_config, context, db):
        self.calls.append(("prepare", len(context) >= 0))

    def write(self, rows, output_config, context, db):
        self.calls.append(("write", list(rows)))
        return SinkResult(inserted=len(rows))

    def flush(self, output_config, context, db):
        self.calls.append(("flush", None))
        return SinkResult()


class TestSinkRegistry:
    def test_default_registry_keys_match_constant(self):
        registry = create_default_sink_registry()
        assert registry.keys() == SINK_KEYS

    def test_register_get_all_close(self):
        registry = SinkRegistry()
        sink = _RecordingSink("target_file")
        registry.register("target_file", sink)

        assert registry.get("target_file") is sink
        assert registry.keys() == {"target_file"}
        assert registry.all() == [sink]

    def test_get_unknown_key_raises(self):
        registry = SinkRegistry()
        with pytest.raises(ValueError):
            registry.get("target_table")

    def test_all_deduplicates_instances(self):
        registry = SinkRegistry()
        sink = _RecordingSink("target_file")
        registry.register("target_file", sink)
        registry.register("alias", sink)
        assert registry.all() == [sink]

    def test_close_is_best_effort(self):
        closed = []

        class _Closer(OutputSink):
            key = "target_file"

            def prepare(self, *a):
                pass

            def write(self, *a):
                return SinkResult()

            def flush(self, *a):
                return SinkResult()

            def close(self):
                closed.append(True)

        registry = SinkRegistry()
        registry.register("target_file", _Closer())
        registry.close()
        assert closed == [True]


class TestPipelineDispatch:
    @pytest.fixture
    def pipeline(self):
        return DataPipeline(parser=Parser(), cleaner=Cleaner())

    def test_only_matching_sinks_invoked(self, pipeline):
        recorder = _RecordingSink("target_file")
        registry = SinkRegistry()
        registry.register("target_file", recorder)
        pipeline = DataPipeline(parser=Parser(), cleaner=Cleaner(), sinks=registry)

        raw = [{"app_id": 1}, {"app_id": 2}]
        output_config = {
            "target_file": {"path": "out.csv", "format": "csv"},
            "parser": {
                "type": "sdk_mapping",
                "fields": [{"name": "app_id", "source": "app_id", "to_number": True}],
            },
        }
        result = pipeline.process(raw, output_config, None, {})

        assert [c[0] for c in recorder.calls] == ["prepare", "write", "flush"]
        assert result.inserted == 2
        assert result.total == 2

    def test_missing_sink_key_is_noop(self, pipeline):
        registry = SinkRegistry()
        registry.register("target_file", _RecordingSink("target_file"))
        pipeline = DataPipeline(parser=Parser(), cleaner=Cleaner(), sinks=registry)

        output_config = {
            "target_mysql": "remote_table",
            "parser": {
                "type": "sdk_mapping",
                "fields": [{"name": "app_id", "source": "app_id"}],
            },
        }
        result = pipeline.process([{"app_id": 1}], output_config, None, {})
        # No sink claims target_mysql here -> nothing written, but rows counted.
        assert result.inserted == 0
        assert result.total == 1


class TestSqliteSink:
    def test_prepare_write_flush(self, db):
        sink = SqliteSink()
        output_config = {
            "target_table": "sink_sqlite_t",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER",
                     "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "app_id", "type": "INTEGER"},
                ],
            },
        }
        sink.prepare(output_config, {}, db)
        result = sink.write([{"app_id": 1}, {"app_id": 2}], output_config, {}, db)
        assert result.inserted == 2
        assert result.updated == 0
        assert sink.flush(output_config, {}, db) == SinkResult()

        rows = db.conn.execute(
            "SELECT app_id FROM sink_sqlite_t ORDER BY app_id"
        ).fetchall()
        assert [r["app_id"] for r in rows] == [1, 2]

    def test_upsert_counts_updated(self, db):
        sink = SqliteSink()
        output_config = {
            "target_table": "sink_sqlite_u",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER",
                     "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "k", "type": "TEXT", "constraint": "UNIQUE"},
                    {"name": "v", "type": "INTEGER"},
                ],
            },
        }
        sink.prepare(output_config, {}, db)
        sink.write([{"k": "a", "v": 1}], output_config, {}, db)
        result = sink.write([{"k": "a", "v": 2}], output_config, {}, db)
        assert result.inserted == 0
        assert result.updated == 1
        value = db.conn.execute("SELECT v FROM sink_sqlite_u").fetchone()["v"]
        assert value == 2

    def test_empty_rows(self, db):
        sink = SqliteSink()
        result = sink.write([], {"target_table": "t"}, {}, db)
        assert result == SinkResult()


# ----------------------------------------------------------------------
# Loader validation
# ----------------------------------------------------------------------

def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _task_yaml(name, output_block):
    return f"""name: {name}
type: api
trigger_type: system
schedule: "0 * * * *"
outputs:
{output_block}
"""


def test_loader_rejects_output_without_sink_key(db, tmp_path):
    root = tmp_path / "tasks"
    # A task whose output has no registered sink key.
    _write(
        root / "g" / "bad.yaml",
        _task_yaml(
            "no_sink",
            "  - parser:\n      type: sdk_mapping\n      fields: [{name: id, source: id}]",
        ),
    )
    loader = TaskLoader(str(root), db)
    assert loader.load_all() == []
    count = db.conn.execute("SELECT COUNT(*) AS n FROM crawl_tasks").fetchone()["n"]
    assert count == 0


def test_loader_accepts_target_mysql_only(db, tmp_path):
    root = tmp_path / "tasks"
    _write(
        root / "g" / "remote.yaml",
        _task_yaml(
            "mysql_only",
            "  - target_mysql: remote_table\n"
            "    parser:\n"
            "      type: sdk_mapping\n"
            "      fields: [{name: id, source: id}]",
        ),
    )
    loader = TaskLoader(str(root), db)
    tasks = loader.load_all()
    assert [t["name"] for t in tasks] == ["mysql_only"]
    row = db.conn.execute(
        "SELECT target_table FROM crawl_tasks WHERE task_name=?", ("mysql_only",)
    ).fetchone()
    assert row["target_table"] == "remote_table"


def test_loader_accepts_target_file_only(db, tmp_path):
    root = tmp_path / "tasks"
    _write(
        root / "g" / "file.yaml",
        _task_yaml(
            "file_only",
            "  - target_file:\n"
            "      path: output/x_{today}.csv\n"
            "      format: csv\n"
            "    parser:\n"
            "      type: sdk_mapping\n"
            "      fields: [{name: id, source: id}]",
        ),
    )
    loader = TaskLoader(str(root), db)
    tasks = loader.load_all()
    assert [t["name"] for t in tasks] == ["file_only"]
