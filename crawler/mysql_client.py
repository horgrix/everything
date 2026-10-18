"""
共享 MySQL 连接构造与连接配置解析。

``DbSource``（读库）与 ``MysqlSink``（写库）原先各拼一份
host/port/user/password/database/charset + DictCursor 的连接参数，
这里收敛为唯一的 :func:`build_mysql_connection`。

``${ENV_VAR}`` 的解析同样只有一份实现（:func:`crawler.template.resolve_env_vars`），
这里提供 :func:`resolve_mysql_config` 把整份 ``mysql`` 连接配置的**字符串字段**
统一走一遍该实现，调用方先解析再建连即可。

注意：解析只认 ``${ENV_VAR}``，不会把值里的 ``{xxx}`` 当模板变量
（密码里出现 ``{xxx}`` 不会被误替换）。
"""

from typing import Any

from .template import resolve_env_vars

# 支持 ``${ENV_VAR}`` 的连接字段（仅对字符串值生效）
MYSQL_ENV_FIELDS = ("host", "port", "user", "password", "database", "charset")


def resolve_mysql_config(db_config: dict) -> dict:
    """解析 MySQL 连接配置里的 ``${ENV_VAR}``，返回**新的** dict。

    - 对 :data:`MYSQL_ENV_FIELDS`（host / port / user / password / database /
      charset）中的**字符串值**调用
      :func:`crawler.template.resolve_env_vars`；
    - 非字符串值（``None``、bool、YAML 里未加引号的 int 等）原样保留；
    - 其余键（如 ``batch_size``）不参与解析，原样带过去；
    - 语义与 ``resolve_env_vars`` 一致：缺失的环境变量保留 ``${VAR}`` 原样并写
      warning，不会静默变空串；值里的普通 ``{xxx}`` 不会被当模板解析。

    传入非 dict（例如 ``None``）时原样返回。
    """
    if not isinstance(db_config, dict):
        return db_config

    resolved = dict(db_config)
    for field in MYSQL_ENV_FIELDS:
        value = resolved.get(field)
        if isinstance(value, str):
            resolved[field] = resolve_env_vars(value)
    return resolved


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

    调用方需保证配置已解析 ``${ENV_VAR}``——通常先调用
    :func:`resolve_mysql_config`。
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
