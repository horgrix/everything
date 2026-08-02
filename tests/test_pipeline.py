"""Tests for DataPipeline."""

import pytest
from crawler.pipeline import DataPipeline, PipelineResult
from crawler.parser import Parser
from crawler.cleaner import Cleaner


class TestPipelineResult:
    """Tests for PipelineResult arithmetic."""

    def test_add(self):
        a = PipelineResult(inserted=5, updated=3, total=8)
        b = PipelineResult(inserted=2, updated=1, total=3)
        c = a + b
        assert c.inserted == 7
        assert c.updated == 4
        assert c.total == 11

    def test_add_empty(self):
        a = PipelineResult()
        b = PipelineResult(inserted=10, total=10)
        c = a + b
        assert c.inserted == 10
        assert c.total == 10


class TestPipeline:
    """Integration tests for DataPipeline.process()."""

    @pytest.fixture
    def pipeline(self):
        return DataPipeline(parser=Parser(), cleaner=Cleaner())

    def test_json_parsing(self, pipeline, db):
        """Parse a simple JSON API response and write to DB."""
        raw_data = '{"title": "Hello", "count": 42}'
        output_config = {
            "target_table": "test_json_table",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER", "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "title", "type": "TEXT"},
                    {"name": "count", "type": "INTEGER"},
                ],
            },
            "parser": {
                "type": "json",
                "fields": [
                    {"name": "title", "path": "title"},
                    {"name": "count", "path": "count", "to_number": True},
                ],
            },
        }

        result = pipeline.process(raw_data, output_config, db, {"url": "http://test"})
        assert result.inserted == 1
        assert result.total == 1

        # Verify the data is in the DB
        rows = db.conn.execute("SELECT * FROM test_json_table").fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "Hello"
        assert rows[0]["count"] == 42

    def test_empty_data(self, pipeline, db):
        """Empty parsed data returns zero result."""
        output_config = {
            "target_table": "test_empty",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER", "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "title", "type": "TEXT"},
                ],
            },
            "parser": {
                "type": "json",
                "root_path": "nonexistent",
                "fields": [{"name": "title", "path": "title"}],
            },
        }
        result = pipeline.process('{"data": []}', output_config, db, {})
        assert result.inserted == 0
        assert result.total == 0

    def test_sdk_mapping(self, pipeline, db):
        """Process already-parsed list[dict] via sdk_mapping."""
        raw_data = [{"original_name": "Alice"}, {"original_name": "Bob"}]
        output_config = {
            "target_table": "test_sdk",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER", "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "name", "type": "TEXT"},
                ],
            },
            "parser": {
                "type": "sdk_mapping",
                "fields": [
                    {"name": "name", "source": "original_name"},
                ],
            },
        }
        result = pipeline.process(raw_data, output_config, db, {})
        assert result.inserted == 2

    def test_source_url_injection(self, pipeline, db):
        """source_url field gets injected from context."""
        raw_data = '[{"name": "test"}]'
        output_config = {
            "target_table": "test_src_url",
            "table_schema": {
                "columns": [
                    {"name": "id", "type": "INTEGER", "constraint": "PRIMARY KEY AUTOINCREMENT"},
                    {"name": "name", "type": "TEXT"},
                    {"name": "source_url", "type": "TEXT"},
                ],
            },
            "parser": {
                "type": "json",
                "fields": [
                    {"name": "name", "path": "name"},
                    {"name": "source_url", "value": "{url}"},
                ],
            },
        }
        result = pipeline.process(raw_data, output_config, db, {"url": "https://example.com"})
        assert result.inserted == 1
        row = db.conn.execute("SELECT source_url FROM test_src_url").fetchone()
        assert row["source_url"] == "https://example.com"
