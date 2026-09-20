"""Tests for the data source abstraction layer."""

import asyncio
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
