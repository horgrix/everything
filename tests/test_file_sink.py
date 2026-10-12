"""Tests for FileSink: real files written then read back for verification."""

import asyncio
import csv
import json
import re

import pytest

from crawler.pipeline import DataPipeline
from crawler.parser import Parser
from crawler.cleaner import Cleaner
from crawler.sinks import create_default_sink_registry
from crawler.sinks.file_sink import FileSink, write_rows_to_file

ROWS = [
    {"app_id": 1, "name": "alpha"},
    {"app_id": 2, "name": "beta"},
]


def _output_config(path, fmt="csv", mode="overwrite"):
    return {
        "target_file": {"path": str(path), "format": fmt, "mode": mode},
        "parser": {
            "type": "sdk_mapping",
            "fields": [
                {"name": "app_id", "source": "app_id", "to_number": True},
                {"name": "name", "source": "name"},
            ],
        },
    }


def test_csv_overwrite_roundtrip(tmp_path):
    path = tmp_path / "out.csv"
    sink = FileSink()
    config = _output_config(path)
    sink.prepare(config, {}, None)
    sink.write(ROWS, config, {}, None)
    result = sink.flush(config, {}, None)

    assert result.sent == 2
    with open(path, "rb") as f:
        assert f.read().startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {"app_id": "1", "name": "alpha"},
        {"app_id": "2", "name": "beta"},
    ]


def test_csv_overwrite_replaces_previous_file(tmp_path):
    path = tmp_path / "out.csv"
    sink = FileSink()
    config = _output_config(path, mode="overwrite")

    sink.write([ROWS[0]], config, {}, None)
    sink.flush(config, {}, None)
    sink.write([ROWS[1]], config, {}, None)
    sink.flush(config, {}, None)

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["name"] for r in rows] == ["beta"]


def test_csv_append_accumulates_across_flushes(tmp_path):
    path = tmp_path / "out.csv"
    sink = FileSink()
    config = _output_config(path, mode="append")

    sink.write([ROWS[0]], config, {}, None)
    sink.flush(config, {}, None)
    sink.write([ROWS[1]], config, {}, None)
    sink.flush(config, {}, None)

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert [r["name"] for r in rows] == ["alpha", "beta"]
    # Header written exactly once.
    with open(path, encoding="utf-8-sig") as f:
        assert f.read().count("app_id") == 1


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "out.jsonl"
    sink = FileSink()
    config = _output_config(path, fmt="jsonl")
    sink.write(ROWS, config, {}, None)
    sink.flush(config, {}, None)

    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    assert rows == ROWS


def test_json_roundtrip_and_append(tmp_path):
    path = tmp_path / "out.json"
    sink = FileSink()
    config = _output_config(path, fmt="json", mode="append")
    sink.write([ROWS[0]], config, {}, None)
    sink.flush(config, {}, None)
    sink.write([ROWS[1]], config, {}, None)
    sink.flush(config, {}, None)

    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    assert rows == ROWS


def test_excel_roundtrip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "out.xlsx"
    sink = FileSink()
    config = _output_config(path, fmt="excel")
    sink.write(ROWS, config, {}, None)
    sink.flush(config, {}, None)

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0] == ["app_id", "name"]
    assert values[1] == [1, "alpha"]
    assert values[2] == [2, "beta"]


def test_excel_append_keeps_single_header(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "out.xlsx"
    sink = FileSink()
    config = _output_config(path, fmt="excel", mode="append")
    sink.write([ROWS[0]], config, {}, None)
    sink.flush(config, {}, None)
    sink.write([ROWS[1]], config, {}, None)
    sink.flush(config, {}, None)

    ws = openpyxl.load_workbook(path).active
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0] == ["app_id", "name"]
    assert values[1] == [1, "alpha"]
    assert values[2] == [2, "beta"]


def test_path_template_resolution(tmp_path):
    from datetime import datetime

    config = {
        "target_file": {
            "path": str(tmp_path / "daily_{today}.csv"),
            "format": "csv",
            "mode": "overwrite",
        },
    }
    sink = FileSink()
    sink.prepare(config, {}, None)
    sink.write(ROWS, config, {}, None)
    sink.flush(config, {}, None)

    expected = tmp_path / f"daily_{datetime.now().strftime('%Y-%m-%d')}.csv"
    assert expected.exists()
    assert re.match(r"daily_\d{4}-\d{2}-\d{2}\.csv", expected.name)


def test_bad_format_raises(tmp_path):
    with pytest.raises(ValueError):
        write_rows_to_file(ROWS, str(tmp_path / "x.txt"), "xml", "overwrite")


def test_pipeline_dispatches_file_sink(tmp_path):
    """Engine-style path: process_async buffers, flush_outputs writes."""
    path = tmp_path / "engine_out.csv"
    pipeline = DataPipeline(
        parser=Parser(), cleaner=Cleaner(), sinks=create_default_sink_registry()
    )
    config = _output_config(path, mode="overwrite")
    raw = [{"app_id": 1, "name": "alpha"}, {"app_id": 2, "name": "beta"}]

    result = asyncio.run(pipeline.process_async(raw, config, None, {}))
    assert result.total == 2
    assert result.api_sent == 0  # not written until flush

    flushed = asyncio.run(pipeline.flush_outputs([config], {}, None))
    assert flushed.api_sent == 2

    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["name"] for r in rows] == ["alpha", "beta"]
