"""Tests for 共享 / 分层参数解析（ParamResolver）。"""

import asyncio
import logging
import sqlite3
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

from crawler.params import ParamResolver, deep_merge
from crawler.sources.db_source import DbSource


def _resolve(resolver, *args, **kwargs):
    """同步测试里驱动异步 ParamResolver.resolve。"""
    return asyncio.run(resolver.resolve(*args, **kwargs))


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _resolver(tmp_path, global_yaml=None, group_yaml=None):
    """构造 ParamResolver：config_dir=<tmp>/config/tasks，全局在 <tmp>/config/params.yaml。"""
    config_dir = tmp_path / "config" / "tasks"
    config_dir.mkdir(parents=True, exist_ok=True)
    if global_yaml is not None:
        _write(tmp_path / "config" / "params.yaml", global_yaml)
    if group_yaml is not None:
        _write(config_dir / "g1" / "params.yaml", group_yaml)
    return ParamResolver(str(config_dir))


# ----------------------------------------------------------------------
# deep_merge
# ----------------------------------------------------------------------

class TestDeepMerge:
    def test_nested_dict_merged(self):
        assert deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}}) == {
            "a": {"x": 1, "y": 3}
        }

    def test_list_is_replaced_not_concatenated(self):
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_scalar_override(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}


# ----------------------------------------------------------------------
# 三层合并优先级
# ----------------------------------------------------------------------

class TestLayerPriority:
    def test_task_overrides_group_and_global(self, tmp_path):
        r = _resolver(
            tmp_path,
            global_yaml="region: global\nlevel: global_only\n",
            group_yaml="region: group\nlevel: group_only\n",
        )
        task_config = {"_group": "g1", "params": {"region": "task"}}
        result = _resolve(r, task_config)
        # 任务层最高，覆盖分组层与全局层
        assert result["region"] == "task"
        # 分组层覆盖全局层
        assert result["level"] == "group_only"

    def test_group_overrides_global(self, tmp_path):
        r = _resolver(
            tmp_path,
            global_yaml="region: global\nkeep: g\n",
            group_yaml="region: group\n",
        )
        result = _resolve(r, {"_group": "g1"})
        assert result["region"] == "group"
        assert result["keep"] == "g"

    def test_task_without_params_returns_global_and_group(self, tmp_path):
        r = _resolver(
            tmp_path,
            global_yaml="a: 1\nnested:\n  x: 1\n",
            group_yaml="b: 2\nnested:\n  y: 2\n",
        )
        result = _resolve(r, {"_group": "g1"})  # 无 params
        assert result == {"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}

    def test_missing_group_params_is_silent(self, tmp_path):
        """分组 params.yaml 不存在时不报错，只返回全局 + 任务。"""
        r = _resolver(tmp_path, global_yaml="a: 1\n", group_yaml=None)
        result = _resolve(r, {"_group": "no_such_group", "params": {"b": 2}})
        assert result == {"a": 1, "b": 2}

    def test_no_group_attribute_is_silent(self, tmp_path):
        """task_config 没有 _group 时跳过分组层，不报错。"""
        r = _resolver(tmp_path, global_yaml="a: 1\n", group_yaml="b: 2\n")
        result = _resolve(r, {"params": {"c": 3}})
        assert result == {"a": 1, "c": 3}

    def test_no_files_at_all(self, tmp_path):
        r = _resolver(tmp_path)
        assert _resolve(r, {"params": {"a": 1}}) == {"a": 1}
        assert _resolve(r, {}) == {}


# ----------------------------------------------------------------------
# 嵌套 dict / list
# ----------------------------------------------------------------------

class TestNested:
    def test_nested_dict_merged_across_layers(self, tmp_path):
        r = _resolver(
            tmp_path,
            global_yaml="db:\n  host: h\n  port: 1\n  opts:\n    a: 1\n",
            group_yaml="db:\n  port: 2\n",
        )
        result = _resolve(r, {"_group": "g1", "params": {"db": {"opts": {"b": 2}}}})
        assert result["db"] == {"host": "h", "port": 2, "opts": {"a": 1, "b": 2}}

    def test_list_value_is_replaced(self, tmp_path):
        r = _resolver(tmp_path, global_yaml="regions:\n  - cn\n  - us\n")
        result = _resolve(r, {"_group": "g1", "params": {"regions": ["jp"]}})
        assert result["regions"] == ["jp"]

    def test_nested_list_of_dicts(self, tmp_path):
        r = _resolver(tmp_path)
        result = _resolve(r, 
            {"params": {"items": [{"k": "${MY_ENV_VAR}"}, {"k": 2}]}}
        )
        assert result["items"] == [{"k": "${MY_ENV_VAR}"}, {"k": 2}]

    def test_regression_reads_file_each_resolve(self, tmp_path):
        """每次 resolve 重新读文件：改了 params.yaml 立即生效。"""
        r = _resolver(tmp_path, global_yaml="v: 1\n")
        assert _resolve(r, {})["v"] == 1
        _write(tmp_path / "config" / "params.yaml", "v: 2\n")
        assert _resolve(r, {})["v"] == 2


# ----------------------------------------------------------------------
# ${ENV} / 模板替换
# ----------------------------------------------------------------------

class TestValueResolution:
    def test_env_var_substituted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAPTAP_KEY", "secret123")
        r = _resolver(tmp_path, global_yaml="api_key: ${TAPTAP_KEY}\n")
        assert _resolve(r, {})["api_key"] == "secret123"

    def test_missing_env_preserved(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NO_SUCH_VAR_XYZ", raising=False)
        r = _resolver(tmp_path, global_yaml="api_key: ${NO_SUCH_VAR_XYZ}\n")
        assert _resolve(r, {})["api_key"] == "${NO_SUCH_VAR_XYZ}"

    def test_time_template_resolved(self, tmp_path):
        from datetime import datetime

        r = _resolver(tmp_path, global_yaml='stat_date: "{today}"\n')
        assert _resolve(r, {})["stat_date"] == datetime.now().strftime("%Y-%m-%d")

    def test_env_in_list_and_nested(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NESTED_ENV", "ok")
        r = _resolver(
            tmp_path,
            global_yaml="outer:\n  inner: ${NESTED_ENV}\nlist:\n  - ${NESTED_ENV}\n",
        )
        result = _resolve(r, {})
        assert result["outer"]["inner"] == "ok"
        assert result["list"] == ["ok"]


# ----------------------------------------------------------------------
# 引擎集成：三层参数合并后进入 context
# ----------------------------------------------------------------------

class TestEngineIntegration:
    def test_engine_merges_layered_params(self, tmp_path):
        import asyncio

        from crawler.engine import CrawlerEngine
        from crawler.sources.base import SourceRegistry, DataSource
        from storage.database import Database

        class _Capture(DataSource):
            def __init__(self):
                self.ctx = None

            async def fetch(self, task_config, context):
                self.ctx = dict(context)
                return [{"id": 1}]

        config_dir = tmp_path / "config" / "tasks"
        (config_dir / "g1").mkdir(parents=True)
        (tmp_path / "config" / "params.yaml").write_text(
            "region: global\nstat_date: '{yesterday}'\nglobal_only: g\n",
            encoding="utf-8",
        )
        (config_dir / "g1" / "params.yaml").write_text(
            "region: group\ngroup_only: gr\n", encoding="utf-8"
        )

        src = _Capture()
        registry = SourceRegistry()
        registry.register("api", src)
        engine = CrawlerEngine(registry, config_dir=str(config_dir))
        task = {
            "name": "t",
            "type": "api",
            "url": "http://x/{region}/{stat_date}",
            "_group": "g1",
            "params": {"region": "task", "extra": "e"},
            "outputs": [],
        }
        db = Database(":memory:")
        db.init_system_tables()
        asyncio.run(engine.run(task, db))

        # 任务层覆盖分组层与全局层
        assert src.ctx["region"] == "task"
        assert src.ctx["extra"] == "e"
        assert src.ctx["global_only"] == "g"
        assert src.ctx["group_only"] == "gr"
        # {yesterday} 模板在 param 值中被解析
        from datetime import datetime, timedelta

        assert src.ctx["stat_date"] == (
            datetime.now() - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        # url 模板使用合并后的 region
        assert "task" in src.ctx["url"]


# ----------------------------------------------------------------------
# P4：查库参数（支持外部库）
# ----------------------------------------------------------------------


def _make_sqlite(tmp_path, name="cfg.db"):
    """建一个临时 sqlite 库并写入测试数据，返回路径字符串。"""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE torchlight_season (ss INTEGER)")
    conn.executemany(
        "INSERT INTO torchlight_season (ss) VALUES (?)", [(10,), (30,), (20,)]
    )
    conn.execute("CREATE TABLE app_ids (app_id TEXT, name TEXT)")
    conn.executemany(
        "INSERT INTO app_ids (app_id, name) VALUES (?, ?)",
        [("a1", "x"), ("a2", "y")],
    )
    conn.commit()
    conn.close()
    return str(path)


def _sqlite_db(path):
    return {"type": "sqlite", "path": path}


class TestQueryParamsLocal:
    """本地 sqlite 查库：真实执行，非 mock。"""

    def test_scalar_first_row_first_column(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "active_ss": {
                        "query": "SELECT MAX(ss) FROM torchlight_season",
                        "db": _sqlite_db(db_path),
                    }
                }
            },
        )
        assert result["active_ss"] == 30

    def test_default_db_config_is_local_crawler_db(self, tmp_path, monkeypatch):
        """未写 db 时缺省查本地 crawler.db（此处 mock 掉真实连接）。"""
        captured = {}

        def fake_read_sync(cfg):
            captured.update(cfg)
            return [{"v": 1}]

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))
        r = _resolver(tmp_path)
        result = _resolve(r, {"params": {"x": {"query": "SELECT 1"}}})
        assert result["x"] == 1
        assert captured["type"] == "sqlite"
        assert captured["path"] == "crawler.db"
        assert captured["query"] == "SELECT 1"

    def test_column_specified(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "name": {
                        "query": "SELECT app_id, name FROM app_ids ORDER BY app_id",
                        "db": _sqlite_db(db_path),
                        "column": "name",
                    }
                }
            },
        )
        assert result["name"] == "x"

    def test_many_returns_list(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "all_ids": {
                        "query": "SELECT app_id FROM app_ids ORDER BY app_id",
                        "db": _sqlite_db(db_path),
                        "column": "app_id",
                        "many": True,
                    }
                }
            },
        )
        assert result["all_ids"] == ["a1", "a2"]

    def test_many_without_column_uses_first_column(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "all_ss": {
                        "query": "SELECT ss FROM torchlight_season ORDER BY ss",
                        "db": _sqlite_db(db_path),
                        "many": True,
                    }
                }
            },
        )
        assert result["all_ss"] == [10, 20, 30]

    def test_engine_puts_query_param_into_context(self, tmp_path):
        """引擎集成：查库参数进入 context，并可被 URL 模板引用。"""
        from crawler.engine import CrawlerEngine
        from crawler.sources.base import DataSource, SourceRegistry
        from storage.database import Database

        class _Capture(DataSource):
            def __init__(self):
                self.ctx = None

            async def fetch(self, task_config, context):
                self.ctx = dict(context)
                return [{"id": 1}]

        db_path = _make_sqlite(tmp_path)
        src = _Capture()
        registry = SourceRegistry()
        registry.register("api", src)
        engine = CrawlerEngine(registry, config_dir=str(tmp_path / "config" / "tasks"))
        task = {
            "name": "t",
            "type": "api",
            "url": "http://x/{active_ss}",
            "params": {
                "active_ss": {
                    "query": "SELECT MAX(ss) FROM torchlight_season",
                    "db": _sqlite_db(db_path),
                }
            },
            "outputs": [],
        }
        db = Database(":memory:")
        db.init_system_tables()
        asyncio.run(engine.run(task, db))

        assert src.ctx["active_ss"] == 30
        assert "30" in src.ctx["url"]


class TestQueryParamsMysql:
    """外部 MySQL：mock pymysql，验证复用 DbSource 的 mysql 分支与参数传递。"""

    @staticmethod
    def _install_fake_pymysql(monkeypatch, rows):
        capture = {}

        class _FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query):
                capture["query"] = query

            def fetchall(self):
                return rows

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def close(self):
                capture["closed"] = True

        def _connect(**kwargs):
            capture["connect"] = kwargs
            return _FakeConn()

        dict_cursor = object()
        fake = types.SimpleNamespace(
            connect=_connect,
            cursors=types.SimpleNamespace(DictCursor=dict_cursor),
        )
        monkeypatch.setitem(sys.modules, "pymysql", fake)
        return capture, dict_cursor

    def test_mysql_branch_and_params(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MYSQL_PWD", "s3cret")
        capture, dict_cursor = self._install_fake_pymysql(
            monkeypatch, [{"v": "on"}]
        )
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "remote_flag": {
                        "query": "SELECT v FROM cfg WHERE k = 'flag'",
                        "db": {
                            "type": "mysql",
                            "host": "10.0.0.5",
                            "port": 3306,
                            "user": "u",
                            "password": "${MYSQL_PWD}",
                            "database": "d",
                        },
                    }
                }
            },
        )

        assert result["remote_flag"] == "on"
        conn_kwargs = capture["connect"]
        assert conn_kwargs["host"] == "10.0.0.5"
        assert conn_kwargs["port"] == 3306
        assert conn_kwargs["user"] == "u"
        assert conn_kwargs["password"] == "s3cret"  # ${ENV} 已解析
        assert conn_kwargs["database"] == "d"
        assert conn_kwargs["cursorclass"] is dict_cursor
        assert capture["query"] == "SELECT v FROM cfg WHERE k = 'flag'"
        assert capture["closed"] is True

    def test_mysql_query_template_resolved_before_execute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CFG_TABLE", "cfg")
        capture, _ = self._install_fake_pymysql(monkeypatch, [{"v": 7}])
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "x": {
                        "query": "SELECT v FROM ${CFG_TABLE} WHERE d = '{today}'",
                        "db": {
                            "type": "mysql",
                            "host": "h",
                            "user": "u",
                            "database": "d",
                        },
                    }
                }
            },
        )
        assert result["x"] == 7
        assert capture["query"] == (
            "SELECT v FROM cfg WHERE d = '"
            + datetime.now().strftime("%Y-%m-%d")
            + "'"
        )


class TestQueryParamsFallback:
    """失败 / 空结果 + default / None / required。"""

    def test_empty_result_without_default_is_none(self, tmp_path, caplog):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        with caplog.at_level(logging.WARNING, logger="crawler.params"):
            result = _resolve(
                r,
                {
                    "params": {
                        "x": {
                            "query": "SELECT ss FROM torchlight_season WHERE 1 = 0",
                            "db": _sqlite_db(db_path),
                        }
                    }
                },
            )
        assert result["x"] is None
        assert any("x" in rec.getMessage() for rec in caplog.records)

    def test_empty_result_with_default(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "x": {
                        "query": "SELECT ss FROM torchlight_season WHERE 1 = 0",
                        "db": _sqlite_db(db_path),
                        "default": 99,
                    }
                }
            },
        )
        assert result["x"] == 99

    def test_default_value_template_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FALLBACK", "fb")
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "x": {
                        "query": "SELECT * FROM nope",
                        "db": {"type": "sqlite", "path": str(tmp_path / "missing.db")},
                        "default": "${FALLBACK}",
                    }
                }
            },
        )
        assert result["x"] == "fb"

    def test_query_failure_without_default_is_none_and_warns(
        self, tmp_path, caplog
    ):
        r = _resolver(tmp_path)
        with caplog.at_level(logging.WARNING, logger="crawler.params"):
            result = _resolve(
                r,
                {
                    "params": {
                        "x": {
                            "query": "SELECT * FROM no_such_table",
                            "db": {
                                "type": "sqlite",
                                "path": str(tmp_path / "cfg.db"),
                            },
                        }
                    }
                },
            )
        assert result["x"] is None
        assert any("x" in rec.getMessage() for rec in caplog.records)

    def test_required_on_failure_raises(self, tmp_path):
        r = _resolver(tmp_path)
        with pytest.raises(sqlite3.OperationalError):
            _resolve(
                r,
                {
                    "params": {
                        "x": {
                            "query": "SELECT * FROM no_such_table",
                            "db": {
                                "type": "sqlite",
                                "path": str(tmp_path / "cfg.db"),
                            },
                            "required": True,
                        }
                    }
                },
            )

    def test_required_on_empty_raises_value_error(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        with pytest.raises(ValueError):
            _resolve(
                r,
                {
                    "params": {
                        "x": {
                            "query": "SELECT ss FROM torchlight_season WHERE 1 = 0",
                            "db": _sqlite_db(db_path),
                            "required": True,
                        }
                    }
                },
            )

    def test_required_takes_precedence_over_default(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        with pytest.raises(ValueError):
            _resolve(
                r,
                {
                    "params": {
                        "x": {
                            "query": "SELECT ss FROM torchlight_season WHERE 1 = 0",
                            "db": _sqlite_db(db_path),
                            "default": 1,
                            "required": True,
                        }
                    }
                },
            )

    def test_missing_column_falls_back_to_default(self, tmp_path):
        db_path = _make_sqlite(tmp_path)
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "x": {
                        "query": "SELECT app_id FROM app_ids",
                        "db": _sqlite_db(db_path),
                        "column": "no_such_column",
                        "default": "d",
                    }
                }
            },
        )
        assert result["x"] == "d"


class TestQueryParamsTemplating:
    """query 串支持 ${ENV} / {today}，并可引用已解析的普通参数。"""

    def test_query_env_and_today(self, tmp_path, monkeypatch):
        captured = {}

        def fake_read_sync(cfg):
            captured["query"] = cfg["query"]
            return [{"d": 1}]

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))
        monkeypatch.setenv("MY_TABLE", "torchlight_season")
        monkeypatch.setenv("MY_DB", "from_env.db")
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {
                "params": {
                    "x": {
                        "query": "SELECT * FROM ${MY_TABLE} WHERE d = '{today}'",
                        "db": {"type": "sqlite", "path": "${MY_DB}"},
                    }
                }
            },
        )
        assert captured["query"] == (
            "SELECT * FROM torchlight_season WHERE d = '"
            + datetime.now().strftime("%Y-%m-%d")
            + "'"
        )
        assert result["x"] == 1

    def test_query_references_resolved_plain_param(self, tmp_path, monkeypatch):
        captured = {}

        def fake_read_sync(cfg):
            captured["query"] = cfg["query"]
            return [{"v": 2}]

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {"params": {"tbl": "cfg", "x": {"query": "SELECT v FROM {tbl}"}}},
        )
        assert captured["query"] == "SELECT v FROM cfg"
        assert result["x"] == 2


class TestAsyncContract:
    """resolve 为异步方法，且查库在子线程执行（asyncio.to_thread）。"""

    def test_resolve_is_coroutine_function(self):
        import inspect

        assert inspect.iscoroutinefunction(ParamResolver.resolve)

    def test_query_runs_in_worker_thread(self, tmp_path, monkeypatch):
        import threading

        main_thread = threading.current_thread()
        seen = {}

        def fake_read_sync(cfg):
            seen["thread"] = threading.current_thread()
            return [{"v": 1}]

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))
        r = _resolver(tmp_path)
        _resolve(r, {"params": {"x": {"query": "SELECT 1"}}})
        assert seen["thread"] is not main_thread


class TestNonQueryParamUnchanged:
    """不含 query 的 param 行为完全不变。"""

    def test_nested_dict_without_query_is_plain(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NESTED_ENV", "ok")
        r = _resolver(tmp_path)
        result = _resolve(
            r, {"params": {"nested": {"a": "${NESTED_ENV}", "b": 2}}}
        )
        assert result["nested"] == {"a": "ok", "b": 2}

    def test_nested_query_key_is_not_executed(self, tmp_path, monkeypatch):
        """只有顶层含 query 的 dict 才是查库参数；嵌套的不执行。"""
        called = {"n": 0}

        def fake_read_sync(cfg):
            called["n"] += 1
            return [{"v": 1}]

        monkeypatch.setattr(DbSource, "_read_sync", staticmethod(fake_read_sync))
        r = _resolver(tmp_path)
        result = _resolve(
            r,
            {"params": {"outer": {"inner": {"query": "SELECT 1"}}}},
        )
        assert called["n"] == 0
        assert result["outer"]["inner"]["query"] == "SELECT 1"

    def test_scalar_params_still_resolved(self, tmp_path):
        r = _resolver(tmp_path)
        result = _resolve(r, {"params": {"a": 1, "b": "plain"}})
        assert result == {"a": 1, "b": "plain"}
