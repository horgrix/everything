"""
共享 / 分层参数解析。

参数来源（优先级低 → 高，后者覆盖前者）:
  1. 全局：``<config_dir 的上一级>/params.yaml``（默认即 ``config/params.yaml``）
  2. 分组：``<config_dir>/<group>/params.yaml``（group 取 task_config["_group"]）
  3. 任务：YAML 顶层 ``params:``

ParamResolver 负责「合并 + 环境变量/模板替换」。引擎原有的
``{today}`` / ``{xxx}`` 模板解析逻辑保持不变。

支持的值形式:
  - 字面量：   region: cn
  - 模板：     stat_date: "{yesterday}"
  - 环境变量： api_key: "${TAPTAP_KEY}"
  - 查库取值（P4）：
        active_ss: {query: "SELECT MAX(ss) FROM torchlight_season"}
        remote_flag:
          query: "SELECT v FROM cfg WHERE k = 'flag'"
          db: {type: mysql, host: ..., password: "${MYSQL_PWD}", database: d}

每次 ``resolve()`` 都会重新读取 params 文件，因此修改 params.yaml
无需重启进程即可生效。
"""

import asyncio
import logging
from pathlib import Path

import yaml

from .sources.db_source import DbSource
from .template import URLTemplate

logger = logging.getLogger(__name__)

# 查库参数缺省使用的本地库（相对当前工作目录）
_DEFAULT_DB_CONFIG = {"type": "sqlite", "path": "crawler.db"}


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个 dict，返回新 dict。

    - 两边同一 key 都是 dict：递归合并
    - 其它情况（含 list、标量）：override 直接覆盖 base
    """
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


class ParamResolver:
    """按「全局 → 分组 → 任务」顺序合并参数，并做环境变量/模板替换与分析查库取值。"""

    def __init__(self, config_dir: str = "config/tasks"):
        self.config_dir = Path(config_dir)
        # 全局参数文件位于 config_dir 的上一级（默认 config/params.yaml）
        self.global_params_path = self.config_dir.parent / "params.yaml"

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def _load_yaml(self, path: Path) -> dict:
        """读取一个 params.yaml；不存在 / 解析失败 / 顶层非 dict 时返回 {}。"""
        if not path.is_file():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.warning("params YAML 解析失败: %s - %s", path, e)
            return {}
        except OSError as e:
            logger.warning("params 文件读取失败: %s - %s", path, e)
            return {}

        if data is None:
            return {}
        if not isinstance(data, dict):
            logger.warning("params 文件顶层不是 dict，已忽略: %s", path)
            return {}
        return data

    def _group_params_path(self, group: str) -> Path:
        return self.config_dir / group / "params.yaml"

    # ------------------------------------------------------------------
    # 合并 + 替换 + 查库
    # ------------------------------------------------------------------

    async def resolve(self, task_config: dict, extra_context: dict = None) -> dict:
        """合并三层参数并完成模板替换与查库取值，返回合并后的 dict。

        Args:
            task_config: 任务配置 dict（读取 ``_group`` 与顶层 ``params``）
            extra_context: 额外上下文（如 url_context），供模板变量解析使用
        """
        merged: dict = {}

        # 1. 全局
        merged = deep_merge(merged, self._load_yaml(self.global_params_path))

        # 2. 分组（没有 _group / 文件不存在时静默跳过）
        group = task_config.get("_group")
        if group:
            merged = deep_merge(merged, self._load_yaml(self._group_params_path(group)))

        # 3. 任务
        task_params = task_config.get("params") or {}
        if isinstance(task_params, dict):
            merged = deep_merge(merged, task_params)

        # 4. 拆分为「普通参数」与「查库参数」（顶层值含 query 键即视为查库参数）
        query_params = {k: v for k, v in merged.items() if self._is_query_param(v)}
        plain_params = {k: v for k, v in merged.items() if k not in query_params}

        # 普通参数照旧解析：context = 合并后的参数（非查库值）+ extra_context
        context = dict(plain_params)
        if extra_context:
            context.update(extra_context)
        resolved = self._resolve_values(plain_params, context)

        # 查库参数：query 串用「已解析的普通参数 + extra_context」做模板解析。
        # 注意：此处不保证引用另一个尚未执行的查库参数（其值此时不可见）。
        query_context = dict(resolved)
        if extra_context:
            query_context.update(extra_context)

        for name, spec in query_params.items():
            resolved[name] = await self._resolve_query_param(name, spec, query_context)

        return resolved

    def _resolve_values(self, value, context: dict):
        if isinstance(value, str):
            return URLTemplate.resolve(value, context=context)
        if isinstance(value, dict):
            return {k: self._resolve_values(v, context) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_values(v, context) for v in value]
        return value

    # ------------------------------------------------------------------
    # 查库参数
    # ------------------------------------------------------------------

    @staticmethod
    def _is_query_param(value) -> bool:
        """顶层 param 值为 dict 且含 ``query`` 键 → 查库参数。

        不含 ``query`` 的 dict 仍按普通嵌套参数处理（行为不变）。
        """
        return isinstance(value, dict) and "query" in value

    async def _resolve_query_param(self, name: str, spec: dict, context: dict):
        """执行一个查库参数并返回取值/兜底结果。"""
        # db 配置：缺省为本地 sqlite（crawler.db）；显式 db 子键与 type: db 数据源一致。
        db_config = dict(spec.get("db") or _DEFAULT_DB_CONFIG)

        # query 串支持 ${ENV} / {today} 等模板变量（用已解析的普通参数 + extra_context）。
        exec_config = self._resolve_values(
            {**db_config, "query": spec.get("query", "")}, context
        )
        # 保持 type 为字符串（模板解析不应改变它，这里显式规范一下）
        exec_config["type"] = str(exec_config.get("type", "")).lower()

        has_default = "default" in spec
        required = bool(spec.get("required", False))

        try:
            rows = await asyncio.to_thread(DbSource._read_sync, exec_config)
        except Exception as e:
            return self._query_fallback(
                name, spec, has_default, required, context, f"查询失败: {e}", e
            )

        if not rows:
            return self._query_fallback(
                name, spec, has_default, required, context, "查询结果为空", None
            )

        try:
            return self._extract_value(rows, spec)
        except Exception as e:
            return self._query_fallback(
                name, spec, has_default, required, context, f"取值失败: {e}", e
            )

    def _query_fallback(
        self,
        name: str,
        spec: dict,
        has_default: bool,
        required: bool,
        context: dict,
        reason: str,
        exc,
    ):
        """失败/空结果时的兜底：required 抛错 → default → None(+warning)。"""
        if required:
            if exc is not None:
                raise exc
            raise ValueError(f"查库参数 '{name}' {reason}（required: true）")
        if has_default:
            logger.warning("查库参数 '%s' %s，使用 default", name, reason)
            return self._resolve_values(spec["default"], context)
        logger.warning("查库参数 '%s' %s，已置为 None", name, reason)
        return None

    @staticmethod
    def _extract_value(rows: list, spec: dict):
        """从查询结果中取值。

        - 默认取「第一行第一列」的标量
        - ``column: <列名>`` 指定列
        - ``many: true`` 返回该列所有行的 list
        """
        column = spec.get("column")
        many = bool(spec.get("many", False))

        first = rows[0]
        if column is not None:
            if column not in first:
                raise KeyError(
                    f"列 '{column}' 不存在（可用列：{list(first.keys())}）"
                )
            if many:
                return [row.get(column) for row in rows]
            return first[column]

        # 未指定列：取第一行第一列
        first_column = next(iter(first.keys()))
        if many:
            return [row[first_column] for row in rows]
        return first[first_column]
