"""Tests for group-level file logging (TaskLogHandler)."""

import asyncio

import crawler.task_log as task_log_module
from crawler.task_log import TaskLogHandler, resolve_log_config


def test_resolve_log_config_defaults():
    assert resolve_log_config({}) == {
        "enabled": True, "data": False, "runtime": True, "error": True,
    }


def test_resolve_log_config_overrides():
    cfg = {
        "logging": {"enabled": False, "data": True, "runtime": False, "error": False},
    }
    assert resolve_log_config(cfg) == {
        "enabled": False, "data": True, "runtime": False, "error": False,
    }


def test_log_files_written(tmp_path):
    handler = TaskLogHandler("g1", str(tmp_path))
    asyncio.run(handler.log_data("task1", "hello body"))
    asyncio.run(handler.log_runtime("done summary"))
    asyncio.run(handler.log_error("boom", "bad body"))

    assert (tmp_path / "g1" / "data.log").read_text(encoding="utf-8") == "hello body"
    assert (tmp_path / "g1" / "runtime.log").read_text(encoding="utf-8") == "done summary"
    err = (tmp_path / "g1" / "error.log").read_text(encoding="utf-8")
    assert "boom" in err
    assert "bad body" in err


def test_log_data_overwrites(tmp_path):
    handler = TaskLogHandler("g1", str(tmp_path))
    asyncio.run(handler.log_data("task1", "first"))
    asyncio.run(handler.log_data("task1", "second"))
    assert (tmp_path / "g1" / "data.log").read_text(encoding="utf-8") == "second"


def test_log_data_truncates(monkeypatch, tmp_path):
    monkeypatch.setattr(task_log_module, "DATA_LIMIT_BYTES", 10)
    handler = TaskLogHandler("g1", str(tmp_path))
    asyncio.run(handler.log_data("task1", "hello world body"))
    content = (tmp_path / "g1" / "data.log").read_text(encoding="utf-8")
    assert content.startswith("[TRUNCATED] 原始大小")
    assert "hello worl" in content


def test_log_data_serializes_list(tmp_path):
    handler = TaskLogHandler("g1", str(tmp_path))
    asyncio.run(handler.log_data("task1", [{"a": 1}]))
    content = (tmp_path / "g1" / "data.log").read_text(encoding="utf-8")
    assert '[{"a": 1}]' in content


def test_log_error_without_body(tmp_path):
    handler = TaskLogHandler("g1", str(tmp_path))
    asyncio.run(handler.log_error("boom"))
    assert (tmp_path / "g1" / "error.log").read_text(encoding="utf-8") == "boom"
