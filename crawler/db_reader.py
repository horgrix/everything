"""
外部数据库数据源模块：从 SQLite / MySQL 查询数据，统一返回 list[dict]。

SQLite 使用标准库 sqlite3（无额外依赖）。
MySQL 使用 pymysql（需 pip install pymysql）。

使用方式:
    reader = DatabaseReader()
    rows = reader.read({
        "type": "sqlite",
        "path": "data/source.db",
        "query": "SELECT * FROM daily_trades"
    })
"""

import logging
import os

logger = logging.getLogger(__name__)


class DatabaseReader:
    """
    外部数据库读取器，支持 SQLite 和 MySQL 两种数据库。

    执行 SQL 查询，将结果集转为 list[dict]（键为列名），
    直接传给 parser（使用 sdk_mapping 类型透传）。
    """

    @staticmethod
    def read(db_config: dict) -> list[dict]:
        """
        根据数据库配置执行查询，返回 list[dict]。

        参数:
            db_config: YAML 中的 db 配置块
                {
                    "type": "sqlite" | "mysql",
                    # SQLite 配置
                    "path": "data/source.db",
                    # MySQL 配置
                    "host": "192.168.1.100",
                    "port": 3306,
                    "user": "reader",
                    "password": "${MYSQL_PWD}",      # 支持 ${ENV_VAR} 环境变量
                    "database": "source_db",
                    # 通用
                    "query": "SELECT * FROM table WHERE date >= '2026-01-01'",
                }

        返回:
            list[dict] - 每行一个 dict，键为列名
        """
        db_type = db_config.get("type", "").lower()
        query = db_config.get("query", "")

        if not query:
            raise ValueError("db 配置缺少 query")

        logger.info("数据库查询: %s, query=%s", db_type, query[:200])

        if db_type == "mysql":
            return DatabaseReader._read_mysql(db_config)
        else:
            # 默认按 SQLite 处理
            return DatabaseReader._read_sqlite(db_config)

    @staticmethod
    def _read_sqlite(db_config: dict) -> list[dict]:
        import sqlite3

        path = db_config.get("path", "")
        query = db_config.get("query", "")

        if not path:
            raise ValueError("SQLite 配置缺少 path")

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute(query)
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        logger.info("SQLite 查询完成: %d 行 (%s)", len(rows), path)
        return rows

    @staticmethod
    def _read_mysql(db_config: dict) -> list[dict]:
        try:
            import pymysql
        except ImportError:
            raise ImportError(
                "读取 MySQL 数据库需要安装 pymysql:\n"
                "  pip install pymysql"
            )

        host = db_config.get("host", "localhost")
        port = int(db_config.get("port", 3306))
        user = db_config.get("user", "")
        password = DatabaseReader._resolve_env(db_config.get("password", ""))
        database = db_config.get("database", "")
        query = db_config.get("query", "")

        if not database:
            raise ValueError("MySQL 配置缺少 database")

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

        logger.info("MySQL 查询完成: %d 行 (%s:%s/%s)", len(rows), host, port, database)
        return rows

    @staticmethod
    def _resolve_env(value: str) -> str:
        """解析 ${ENV_VAR} 格式的环境变量引用"""
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            env_var = value[2:-1]
            return os.environ.get(env_var, "")
        return value