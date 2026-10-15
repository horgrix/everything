"""Tests for MysqlSink: pure SQL builders + mocked pymysql execution.

No live MySQL server is required (and none is assumed to exist).
"""

import sys
import types
import asyncio

import pytest

from crawler.sinks.mysql_sink import (
    MysqlSink,
    build_create_table_sql,
    build_index_statements,
    build_upsert_sql,
    suppressed_update_columns,
)
from crawler.sinks.base import SinkResult


# ----------------------------------------------------------------------
# Pure SQL builders (no DB)
# ----------------------------------------------------------------------

class TestBuildCreateTableSql:
    def test_uses_raw_native_dialect(self):
        sql = build_create_table_sql(
            "t",
            [
                {"name": "id", "type": "BIGINT",
                 "constraint": "AUTO_INCREMENT PRIMARY KEY"},
                {"name": "name", "type": "VARCHAR(255)", "constraint": "NOT NULL"},
                {"name": "payload", "type": "JSON"},
            ],
        )
        # Verbatim types/constraints -> no SQLite<->MySQL mapping.
        assert sql.startswith("CREATE TABLE IF NOT EXISTS t (")
        assert "id BIGINT AUTO_INCREMENT PRIMARY KEY" in sql
        assert "name VARCHAR(255) NOT NULL" in sql
        assert "payload JSON" in sql
        assert "INTEGER" not in sql and "TEXT" not in sql

    def test_missing_type_raises(self):
        with pytest.raises(ValueError):
            build_create_table_sql("t", [{"name": "a"}])


class TestBuildIndexStatements:
    def test_unique_and_normal(self):
        stmts = build_index_statements(
            "t",
            [
                {"name": "idx_a", "columns": ["a", "b"], "unique": True},
                {"name": "idx_b", "columns": ["c"]},
            ],
        )
        assert stmts == [
            {"name": "idx_a", "sql": "CREATE UNIQUE INDEX idx_a ON t (a, b)"},
            {"name": "idx_b", "sql": "CREATE INDEX idx_b ON t (c)"},
        ]

    def test_empty(self):
        assert build_index_statements("t", None) == []


class TestSuppressedUpdateColumns:
    def test_detects_pk_and_auto_increment(self):
        schema = {
            "columns": [
                {"name": "id", "type": "BIGINT",
                 "constraint": "AUTO_INCREMENT PRIMARY KEY"},
                {"name": "k", "type": "VARCHAR(64)", "constraint": "PRIMARY KEY"},
                {"name": "v", "type": "INT"},
            ],
        }
        assert suppressed_update_columns(schema) == {"id", "k"}

    def test_no_constraints(self):
        assert suppressed_update_columns(
            {"columns": [{"name": "v", "type": "INT"}]}
        ) == set()


class TestBuildUpsertSql:
    def test_excludes_primary_key_from_update(self):
        sql = build_upsert_sql("t", ["id", "app_id", "name"], {"id"})
        assert sql == (
            "INSERT INTO t (id, app_id, name) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE app_id=VALUES(app_id), name=VALUES(name)"
        )

    def test_all_columns_suppressed_falls_back_to_insert_ignore(self):
        sql = build_upsert_sql("t", ["id"], {"id"})
        assert sql == "INSERT IGNORE INTO t (id) VALUES (%s)"


# ----------------------------------------------------------------------
# Connection / env resolution
# ----------------------------------------------------------------------

class _FakeConn:
    def __init__(self, before_after=None, existing_indexes=None):
        self.counts = list(before_after or [])
        self.existing_indexes = set(existing_indexes or [])
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        self.conn.executed.append((sql, args))
        if "COUNT(*)" in sql:
            self._rows = [{"n": self.conn.counts.pop(0)}]
        elif "information_schema.statistics" in sql:
            self._rows = [{"INDEX_NAME": n} for n in self.conn.existing_indexes]
        else:
            self._rows = []

    def executemany(self, sql, seq):
        self.conn.executed.append((sql, list(seq)))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _output_config(batch_size=0, schema=None):
    return {
        "target_mysql": "remote_t",
        "mysql": {"host": "h", "port": 3306, "user": "u",
                  "database": "d", "batch_size": batch_size},
        "table_schema": schema or {
            "columns": [
                {"name": "id", "type": "BIGINT",
                 "constraint": "AUTO_INCREMENT PRIMARY KEY"},
                {"name": "app_id", "type": "BIGINT"},
                {"name": "name", "type": "VARCHAR(255)"},
            ],
        },
    }


def _bind(sink, conn):
    return lambda output_config: conn


def test_password_env_resolution(monkeypatch):
    captured = {}
    fake_pymysql = types.SimpleNamespace(
        connect=lambda **kw: (captured.update(kw) or _FakeConn()),
        cursors=types.SimpleNamespace(DictCursor=object),
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setenv("MY_SQL_PW", "s3cret")

    sink = MysqlSink()
    sink._get_connection(
        {"mysql": {"host": "h", "database": "d", "password": "${MY_SQL_PW}"}}
    )
    assert captured["password"] == "s3cret"
    assert captured["database"] == "d"
    assert captured["charset"] == "utf8mb4"


def test_missing_database_raises(monkeypatch):
    fake_pymysql = types.SimpleNamespace(
        connect=lambda **kw: _FakeConn(),
        cursors=types.SimpleNamespace(DictCursor=object),
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    with pytest.raises(ValueError):
        MysqlSink()._get_connection({"mysql": {"host": "h"}})


def test_password_env_substring_and_missing(monkeypatch):
    """密码复用唯一 ${ENV} 语义：子串替换；缺失保留原样，不静默变空。"""
    captured = {}
    fake_pymysql = types.SimpleNamespace(
        connect=lambda **kw: (captured.update(kw) or _FakeConn()),
        cursors=types.SimpleNamespace(DictCursor=object),
    )
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setenv("WRITE_PW", "s3cret")

    MysqlSink()._get_connection(
        {"mysql": {"host": "h", "database": "d", "password": "pre-${WRITE_PW}"}}
    )
    assert captured["password"] == "pre-s3cret"

    captured.clear()
    monkeypatch.delenv("WRITE_PW", raising=False)
    MysqlSink()._get_connection(
        {"mysql": {"host": "h", "database": "d", "password": "pre-${WRITE_PW}"}}
    )
    assert captured["password"] == "pre-${WRITE_PW}"


# ----------------------------------------------------------------------
# prepare / write / flush with a fake connection
# ----------------------------------------------------------------------

def test_prepare_creates_table_and_skips_existing_index(monkeypatch):
    sink = MysqlSink()
    conn = _FakeConn(existing_indexes={"idx_app_id"})
    monkeypatch.setattr(sink, "_get_connection", _bind(sink, conn))
    output_config = _output_config(schema={
        "columns": [
            {"name": "id", "type": "BIGINT",
             "constraint": "AUTO_INCREMENT PRIMARY KEY"},
            {"name": "app_id", "type": "BIGINT"},
        ],
        "indexes": [
            {"name": "idx_app_id", "columns": ["app_id"], "unique": True},
            {"name": "idx_new", "columns": ["id"]},
        ],
    })

    sink.prepare(output_config, {}, None)
    executed_sql = [sql for sql, _ in conn.executed]
    assert any(sql.startswith("CREATE TABLE IF NOT EXISTS remote_t") for sql in executed_sql)
    # Existing index skipped, missing one created.
    assert "CREATE UNIQUE INDEX idx_app_id ON remote_t (app_id)" not in executed_sql
    assert "CREATE INDEX idx_new ON remote_t (id)" in executed_sql
    assert conn.commits == 1


def test_flush_executes_upsert_and_counts(monkeypatch):
    sink = MysqlSink()
    conn = _FakeConn(before_after=[5, 7])
    monkeypatch.setattr(sink, "_get_connection", _bind(sink, conn))
    output_config = _output_config()

    rows = [
        {"id": 1, "app_id": 10, "name": "a"},
        {"id": 2, "app_id": 20, "name": "b"},
    ]
    assert sink.write(rows, output_config, {}, None) == SinkResult()
    result = sink.flush(output_config, {}, None)

    assert result == SinkResult(inserted=2, updated=0)
    upsert = [e for e in conn.executed if e[0].startswith("INSERT INTO remote_t")]
    assert len(upsert) == 1
    sql, seq = upsert[0]
    assert "ON DUPLICATE KEY UPDATE app_id=VALUES(app_id), name=VALUES(name)" in sql
    assert "id=VALUES(id)" not in sql
    assert seq == [[1, 10, "a"], [2, 20, "b"]]
    assert conn.commits == 1


def test_batch_size_flushes_full_batches(monkeypatch):
    sink = MysqlSink()
    conn = _FakeConn(before_after=[0, 2, 2, 3])
    monkeypatch.setattr(sink, "_get_connection", _bind(sink, conn))
    output_config = _output_config(batch_size=2)

    rows = [
        {"id": 1, "app_id": 1, "name": "a"},
        {"id": 2, "app_id": 2, "name": "b"},
        {"id": 3, "app_id": 3, "name": "c"},
    ]
    # write() drains the first full batch...
    first = sink.write(rows, output_config, {}, None)
    assert first.inserted == 2
    # ...flush() sends the remainder.
    second = sink.flush(output_config, {}, None)
    assert second.inserted == 1

    upserts = [e for e in conn.executed if e[0].startswith("INSERT INTO remote_t")]
    assert len(upserts) == 2
    assert upserts[0][1] == [[1, 1, "a"], [2, 2, "b"]]
    assert upserts[1][1] == [[3, 3, "c"]]


def test_flush_failure_reports_failed(monkeypatch):
    sink = MysqlSink()
    conn = _FakeConn(before_after=[0])
    monkeypatch.setattr(sink, "_get_connection", _bind(sink, conn))
    output_config = _output_config()

    def boom(sql, seq):
        raise RuntimeError("db down")

    monkeypatch.setattr(conn, "cursor", lambda: _FailingCursor(conn, boom))
    sink.write([{"id": 1, "app_id": 1, "name": "a"}], output_config, {}, None)
    result = sink.flush(output_config, {}, None)
    assert result.failed == 1
    assert conn.rollbacks == 1


class _FailingCursor(_FakeCursor):
    def __init__(self, conn, on_executemany):
        super().__init__(conn)
        self._on_executemany = on_executemany

    def executemany(self, sql, seq):
        self._on_executemany(sql, seq)


def test_async_facade_offloads_to_thread(monkeypatch):
    """The *_async facades run the sync pymysql path in a worker thread."""
    sink = MysqlSink()
    conn = _FakeConn(before_after=[0, 1])
    monkeypatch.setattr(sink, "_get_connection", _bind(sink, conn))
    output_config = _output_config()

    async def go():
        await sink.write_async(
            [{"id": 1, "app_id": 1, "name": "a"}], output_config, {}, None
        )
        return await sink.flush_async(output_config, {}, None)

    result = asyncio.run(go())
    assert result.inserted == 1
    upserts = [e for e in conn.executed if e[0].startswith("INSERT INTO remote_t")]
    assert len(upserts) == 1
