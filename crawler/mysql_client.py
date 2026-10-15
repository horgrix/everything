"""
共享 MySQL 连接构造。

``DbSource``（读库）与 ``MysqlSink``（写库）原先各拼一份
host/port/user/password/database/charset + DictCursor 的连接参数，
这里收敛为唯一的 :func:`build_mysql_connection`。

密码等 ``${ENV_VAR}`` 的解析由调用方通过
:func:`crawler.template.resolve_env_vars` 完成（唯一实现），本模块只负责建连。
"""

from typing import Any


def build_mysql_connection(
    db_config: dict,
    *,
    charset: str | None = None,
    read_timeout: int | None = None,
    connect_timeout: int = 10,
) -> Any:
    """按 ``db_config`` 构造 pymysql 连接（``DictCursor``，utf8mb4 默认）。

    参数：
        db_config: 含 host / port / user / password / database（可选 charset）
        charset: 覆盖 ``db_config['charset']``；都缺省时用 ``utf8mb4``
        read_timeout: 秒；``None`` 表示不设置（写库路径默认不设）
        connect_timeout: 连接超时秒数

    调用方需保证 ``db_config['password']`` 已解析 ``${ENV_VAR}``。
    """
    try:
        import pymysql
    except ImportError:
        raise ImportError(
            "操作 MySQL 需要 pymysql:\n"
            "  pip install pymysql"
        )

    database = db_config.get("database", "")
    if not database:
        raise ValueError("MySQL config missing 'database'")

    kwargs = dict(
        host=db_config.get("host", "localhost"),
        port=int(db_config.get("port", 3306) or 3306),
        user=db_config.get("user", ""),
        password=db_config.get("password", ""),
        database=database,
        charset=charset or db_config.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=connect_timeout,
    )
    if read_timeout is not None:
        kwargs["read_timeout"] = read_timeout

    return pymysql.connect(**kwargs)
