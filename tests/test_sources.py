"""Tests for the data source abstraction layer."""

import asyncio
import sys
import types
from datetime import datetime

import pytest
from crawler.sources.base import SourceRegistry
from crawler.sources.http_source import HttpSource
from crawler.sources.sdk_source import SdkSource
from crawler.sources.file_source import FileSource
from crawler.sources.db_source import DbSource


class TestSourceRegistry:
    """Tests for SourceRegistry."""

    def test_register_and_get(self):
        registry = SourceRegistry()
        registry.register("api", HttpSource())
        registry.register("csv", FileSource())
        assert isinstance(registry.get("api"), HttpSource)
        assert isinstance(registry.get("csv"), FileSource)

    def test_unknown_type_raises(self):
        registry = SourceRegistry()
        with pytest.raises(ValueError, match="Unknown source type"):
            registry.get("nonexistent")

    def test_get_all_types(self):
        registry = SourceRegistry()
        registry.register("sdk", SdkSource())
        registry.register("csv", FileSource())
        assert registry.get_all_types() == ["csv", "sdk"]


class TestSdkNormalize:
    """Tests for SdkSource._normalize."""

    def test_list_of_dicts_passthrough(self):
        data = [{"a": 1}, {"a": 2}]
        result = SdkSource._normalize(data)
        assert result == data

    def test_single_dict_wraps(self):
        data = {"key": "value"}
        result = SdkSource._normalize(data)
        assert result == [{"key": "value"}]

    def test_empty_list(self):
        assert SdkSource._normalize([]) == []

    def test_none_returns_empty(self):
        assert SdkSource._normalize(None) == []

    def test_scalar_wraps(self):
        result = SdkSource._normalize(42)
        assert result == [{"value": 42}]


class TestDbSourceQueryTemplate:
    """Tests for template variable resolution in DbSource.query."""

    def test_query_resolves_template(self, monkeypatch):
        captured = {}

        def fake_read_sync(db_config):
            captured["query"] = db_config.get("query", "")
            return []

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))

        source = DbSource()
        query = "SELECT * FROM t WHERE d = '{today}'"
        asyncio.run(
            source.fetch(
                {
                    "type": "db",
                    "db": {"type": "sqlite", "path": "x.db", "query": query},
                },
                {"task_name": "t"},
            )
        )

        expected = (
            "SELECT * FROM t WHERE d = '"
            + datetime.now().strftime("%Y-%m-%d")
            + "'"
        )
        assert captured["query"] == expected

    def test_query_resolves_context_var(self, monkeypatch):
        captured = {}

        def fake_read_sync(db_config):
            captured["query"] = db_config.get("query", "")
            return []

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))

        source = DbSource()
        asyncio.run(
            source.fetch(
                {"type": "db", "db": {"type": "sqlite", "query": "SELECT {region}"}},
                {"region": "CN"},
            )
        )
        assert captured["query"] == "SELECT CN"


class TestFileSourcePathTemplate:
    """file.path 与其它数据源一致，支持模板变量（含 ${ENV}）。"""

    def test_path_resolves_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        (tmp_path / "in.csv").write_text("id,name\n1,a\n", encoding="utf-8")

        source = FileSource()
        rows = asyncio.run(
            source.fetch(
                {
                    "type": "csv",
                    "file": {"format": "csv", "path": "${DATA_DIR}/in.csv"},
                },
                {"task_name": "t"},
            )
        )
        assert rows == [{"id": "1", "name": "a"}]

    def test_path_resolves_date_var(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        (tmp_path / f"{today}.csv").write_text("id\n7\n", encoding="utf-8")

        source = FileSource()
        rows = asyncio.run(
            source.fetch(
                {
                    "type": "csv",
                    "file": {"format": "csv", "path": f"{tmp_path}/{{today}}.csv"},
                },
                {},
            )
        )
        assert rows == [{"id": "7"}]


class TestDbSourceMysqlPasswordEnv:
    """MySQL 密码复用唯一的 ${ENV} 解析（子串替换，缺失保留原样）。"""

    @staticmethod
    def _fake_pymysql(captured):
        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query):
                captured["query"] = query

            def fetchall(self):
                return [{"a": 1}]

        class _Conn:
            def cursor(self):
                return _Cursor()

            def close(self):
                captured["closed"] = True

        return types.SimpleNamespace(
            connect=lambda **kw: (captured.update(kw) or _Conn()),
            cursors=types.SimpleNamespace(DictCursor=object),
        )

    def test_password_env_substring(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", self._fake_pymysql(captured))
        monkeypatch.setenv("READ_PW", "pw")

        rows = DbSource._read_mysql(
            {"host": "h", "database": "d", "password": "x-${READ_PW}",
             "query": "SELECT 1"}
        )
        assert rows == [{"a": 1}]
        assert captured["password"] == "x-pw"
        assert captured["closed"] is True

    def test_missing_password_env_preserved(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", self._fake_pymysql(captured))
        monkeypatch.delenv("READ_PW", raising=False)

        DbSource._read_mysql(
            {"host": "h", "database": "d", "password": "${READ_PW}",
             "query": "SELECT 1"}
        )
        # 不再静默变空串
        assert captured["password"] == "${READ_PW}"
