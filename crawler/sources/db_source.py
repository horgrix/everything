"""
External database data source: queries SQLite / MySQL and returns list[dict].

Handles task type: 'db'.

Uses asyncio.to_thread() for synchronous DB queries.
"""

import asyncio
import logging

from .base import DataSource
from ..mysql_client import build_mysql_connection, resolve_mysql_config
from ..template import URLTemplate

logger = logging.getLogger(__name__)


class DbSource(DataSource):
    """
    External database source — reads from SQLite or MySQL via SQL query.

    DB queries are synchronous and run via asyncio.to_thread().

    Returns list[dict] with column-name keys.

    Supports ${ENV_VAR} substitution in the connection fields
    (host / port / user / password / database / charset).
    """

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> list[dict]:
        """Execute DB query in a thread, return list[dict].

        The `db.query` supports template variables ({today}/{yesterday}/...),
        resolved against the runtime context before execution.
        """
        db_config = dict(task_config.get("db", {}))
        db_config["query"] = URLTemplate.resolve(
            db_config.get("query", ""), context=context or {}
        )
        return await asyncio.to_thread(self._read_sync, db_config)

    # ---- Internal ----

    @staticmethod
    def _read_sync(db_config: dict) -> list[dict]:
        """Synchronous DB query (runs in a thread)."""
        db_type = db_config.get("type", "").lower()
        query = db_config.get("query", "")

        if not query:
            raise ValueError("db config missing 'query'")

        logger.info("DB query: %s, query=%s", db_type, query[:200])

        if db_type == "mysql":
            return DbSource._read_mysql(db_config)
        else:
            return DbSource._read_sqlite(db_config)

    @staticmethod
    def _read_sqlite(db_config: dict) -> list[dict]:
        import sqlite3

        path = db_config.get("path", "")
        query = db_config.get("query", "")

        if not path:
            raise ValueError("SQLite config missing 'path'")

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        logger.info("SQLite query: %d rows (%s)", len(rows), path)
        return rows

    @staticmethod
    def _read_mysql(db_config: dict) -> list[dict]:
        # 先解析整份连接配置的 ${ENV_VAR}（host/port/user/password/database/charset），
        # 解析只认 ${VAR}，密码里的 {xxx} 不会被当模板。
        db_config = resolve_mysql_config(db_config)
        host = db_config.get("host", "localhost")
        port = int(db_config.get("port", 3306) or 3306)
        database = db_config.get("database", "")
        query = db_config.get("query", "")

        conn = build_mysql_connection(db_config, read_timeout=30)

        try:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
        finally:
            conn.close()

        logger.info("MySQL query: %d rows (%s:%s/%s)", len(rows), host, port, database)
        return rows
