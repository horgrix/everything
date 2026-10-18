"""MySQL 连接字段（host/port/user/database/charset）的 ``${ENV_VAR}`` 解析测试。

覆盖：
* :func:`crawler.mysql_client.resolve_mysql_config` 本身的语义；
* 写路径 ``MysqlSink._get_connection``（含连接缓存键 ``_conn_key``）；
* 读路径 ``DbSource._read_mysql``。

全部 mock pymysql，不连真实 MySQL。
"""

import sys
import types

import pytest

from crawler.mysql_client import resolve_mysql_config
from crawler.sinks.mysql_sink import MysqlSink
from crawler.sources.db_source import DbSource


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _fake_pymysql(captured):
    """返回可塞进 sys.modules['pymysql'] 的假模块，记录 connect 的 kwargs。"""
    captured.setdefault("connects", [])

    def _connect(**kwargs):
        captured.update(kwargs)
        captured["connects"].append(kwargs)
        return _FakeConn()

    return types.SimpleNamespace(
        connect=_connect,
        cursors=types.SimpleNamespace(DictCursor=object),
    )


class _FakeConn:
    def __init__(self):
        self.closed = False
        self.queries = []

    def cursor(self):
        outer = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query):
                outer.queries.append(query)

            def fetchall(self):
                return [{"a": 1}]

        return _Cursor()

    def close(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch):
    """一组连接字段的环境变量。"""
    values = {
        "DB_HOST": "db.internal",
        "DB_PORT": "3307",
        "DB_USER": "crawler",
        "DB_NAME": "analytics",
        "DB_CHARSET": "utf8mb4",
        "DB_PASSWORD": "s3cret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


# ----------------------------------------------------------------------
# resolve_mysql_config
# ----------------------------------------------------------------------

class TestResolveMysqlConfig:
    def test_all_string_fields_resolved(self, env):
        cfg = resolve_mysql_config(
            {
                "host": "${DB_HOST}",
                "port": "${DB_PORT}",
                "user": "${DB_USER}",
                "password": "${DB_PASSWORD}",
                "database": "${DB_NAME}",
                "charset": "${DB_CHARSET}",
                "batch_size": 500,
            }
        )
        assert cfg == {
            "host": "db.internal",
            "port": "3307",
            "user": "crawler",
            "password": "s3cret",
            "database": "analytics",
            "charset": "utf8mb4",
            "batch_size": 500,
        }

    def test_returns_new_dict_and_keeps_original(self, env):
        original = {"host": "${DB_HOST}", "database": "d"}
        cfg = resolve_mysql_config(original)
        assert cfg is not original
        assert original["host"] == "${DB_HOST}"

    def test_substring_env_in_each_field(self, env):
        cfg = resolve_mysql_config(
            {
                "host": "pre-${DB_HOST}-x",
                "port": "${DB_PORT}",
                "user": "u_${DB_USER}",
                "database": "db_${DB_NAME}",
                "charset": "${DB_CHARSET}",
            }
        )
        assert cfg["host"] == "pre-db.internal-x"
        assert cfg["user"] == "u_crawler"
        assert cfg["database"] == "db_analytics"

    def test_missing_env_preserved(self, monkeypatch):
        monkeypatch.delenv("NO_SUCH_DB_HOST", raising=False)
        cfg = resolve_mysql_config(
            {"host": "${NO_SUCH_DB_HOST}", "database": "d"}
        )
        # 保留原样，不静默变空串
        assert cfg["host"] == "${NO_SUCH_DB_HOST}"

    def test_password_does_not_expand_braces(self, env):
        cfg = resolve_mysql_config({"password": "p{a}ss-${DB_PASSWORD}"})
        assert cfg["password"] == "p{a}ss-s3cret"

    def test_non_string_values_untouched(self, env):
        cfg = resolve_mysql_config(
            {"host": None, "port": 3306, "user": 123, "database": "d"}
        )
        assert cfg["host"] is None
        assert cfg["port"] == 3306
        assert cfg["user"] == 123

    def test_non_dict_passthrough(self):
        assert resolve_mysql_config(None) is None

    def test_empty_dict(self):
        assert resolve_mysql_config({}) == {}


# ----------------------------------------------------------------------
# 写路径：MysqlSink
# ----------------------------------------------------------------------

class TestMysqlSinkConnectionEnv:
    def test_connect_kwargs_use_resolved_values(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))

        conn = MysqlSink()._get_connection(
            {
                "mysql": {
                    "host": "${DB_HOST}",
                    "port": "${DB_PORT}",
                    "user": "${DB_USER}",
                    "password": "${DB_PASSWORD}",
                    "database": "${DB_NAME}",
                    "charset": "${DB_CHARSET}",
                }
            }
        )
        assert captured["host"] == "db.internal"
        assert captured["port"] == 3307
        assert captured["user"] == "crawler"
        assert captured["password"] == "s3cret"
        assert captured["database"] == "analytics"
        assert captured["charset"] == "utf8mb4"
        assert conn.closed is False

    def test_conn_key_uses_resolved_values(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))
        sink = MysqlSink()

        output_config = {
            "mysql": {
                "host": "${DB_HOST}",
                "port": "${DB_PORT}",
                "user": "${DB_USER}",
                "database": "${DB_NAME}",
            }
        }
        sink._get_connection(output_config)

        (key,) = sink._conns.keys()
        assert key[0] == "db.internal"
        assert key[1] == 3307
        assert key[2] == "crawler"
        assert key[4] == "analytics"
        # 不含任何未解析的 ${...}
        assert not any("${" in str(part) for part in key)

    def test_env_literal_and_template_share_cache_entry(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))
        sink = MysqlSink()

        first = sink._get_connection(
            {"mysql": {"host": "${DB_HOST}", "database": "${DB_NAME}"}}
        )
        second = sink._get_connection(
            {"mysql": {"host": "db.internal", "database": "analytics"}}
        )

        assert first is second
        assert len(sink._conns) == 1
        # 只建连一次（解析后缓存键相同）
        assert len(captured["connects"]) == 1

    def test_env_change_yields_new_key_and_new_connection(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))
        sink = MysqlSink()
        output_config = {
            "mysql": {"host": "${DB_HOST}", "database": "${DB_NAME}"}
        }

        sink._get_connection(output_config)
        monkeypatch.setenv("DB_HOST", "db.other")
        sink._get_connection(output_config)

        assert len(sink._conns) == 2
        assert len(captured["connects"]) == 2
        assert captured["connects"][1]["host"] == "db.other"

    def test_missing_env_kept_verbatim(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))
        monkeypatch.delenv("NOPE_DB_USER", raising=False)

        MysqlSink()._get_connection(
            {"mysql": {"host": "h", "database": "d", "user": "${NOPE_DB_USER}"}}
        )
        assert captured["user"] == "${NOPE_DB_USER}"

    def test_password_behaviour_unchanged(self, monkeypatch, env):
        """密码仍只做 ${ENV} 替换，不解析 {xxx}。"""
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))

        MysqlSink()._get_connection(
            {
                "mysql": {
                    "host": "h",
                    "database": "d",
                    "password": "{today}-${DB_PASSWORD}",
                }
            }
        )
        assert captured["password"] == "{today}-s3cret"


# ----------------------------------------------------------------------
# 读路径：DbSource
# ----------------------------------------------------------------------

class TestDbSourceConnectionEnv:
    def test_read_mysql_resolves_all_fields(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))

        rows = DbSource._read_mysql(
            {
                "type": "mysql",
                "host": "${DB_HOST}",
                "port": "${DB_PORT}",
                "user": "${DB_USER}",
                "password": "${DB_PASSWORD}",
                "database": "${DB_NAME}",
                "charset": "${DB_CHARSET}",
                "query": "SELECT 1",
            }
        )

        assert rows == [{"a": 1}]
        assert captured["host"] == "db.internal"
        assert captured["port"] == 3307
        assert captured["user"] == "crawler"
        assert captured["password"] == "s3cret"
        assert captured["database"] == "analytics"
        assert captured["charset"] == "utf8mb4"
        assert captured["read_timeout"] == 30

    def test_read_mysql_missing_env_kept_verbatim(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))
        monkeypatch.delenv("NOPE_DB_DB", raising=False)

        DbSource._read_mysql(
            {
                "host": "h",
                "database": "${NOPE_DB_DB}",
                "query": "SELECT 1",
            }
        )
        assert captured["database"] == "${NOPE_DB_DB}"

    def test_read_mysql_password_braces_untouched(self, monkeypatch, env):
        captured = {}
        monkeypatch.setitem(sys.modules, "pymysql", _fake_pymysql(captured))

        DbSource._read_mysql(
            {
                "host": "h",
                "database": "d",
                "password": "{now}-${DB_PASSWORD}",
                "query": "SELECT 1",
            }
        )
        assert captured["password"] == "{now}-s3cret"
