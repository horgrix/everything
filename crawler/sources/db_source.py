"""
External database data source: queries SQLite / MySQL and returns list[dict].

Handles task type: 'db'.

Uses asyncio.to_thread() for synchronous DB queries.
"""

import asyncio
import logging
import os
from typing import Any

from .base import DataSource

logger = logging.getLogger(__name__)


class DbSource(DataSource):
    """
    External database source — reads from SQLite or MySQL via SQL query.

    DB queries are synchronous and run via asyncio.to_thread().

    Returns list[dict] with column-name keys.

    Supports ${ENV_VAR} substitution in password fields.
    """

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> list[dict]:
        """Execute DB query in a thread, return list[dict]."""
        db_config = task_config.get("db", {})
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
        try:
            import pymysql
        except ImportError:
            raise ImportError(
                "Reading MySQL requires pymysql:\n"
                "  pip install pymysql"
            )

        host = db_config.get("host", "localhost")
        port = int(db_config.get("port", 3306))
        user = db_config.get("user", "")
        password = DbSource._resolve_env(db_config.get("password", ""))
        database = db_config.get("database", "")
        query = db_config.get("query", "")

        if not database:
            raise ValueError("MySQL config missing 'database'")

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            read_timeout=30,
            connect_timeout=10,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()
        finally:
            conn.close()

        logger.info("MySQL query: %d rows (%s:%s/%s)", len(rows), host, port, database)
        return rows

    @staticmethod
    def _resolve_env(value: str) -> str:
        """Resolve ${ENV_VAR} references in strings."""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var, "")
        return value
