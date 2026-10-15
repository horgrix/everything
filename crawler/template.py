"""
模板变量解析模块。

支持在 YAML 配置的 URL / query / value 等任何字符串中使用运行时动态变量。
引擎在运行时自动替换。

支持的变量:
  时间类:
    {today}                  → 2026-07-18
    {today:format}           → 按 strftime 格式化
    {yesterday}              → 2026-07-17
    {yesterday:format}
    {now}                    → 2026-07-18 20:30:00
    {now:format}
    {timestamp}              → 1753766400（当前 Unix 秒级时间戳）
    {timestamp_ms}           → 1753766400000（当前 Unix 毫秒级时间戳）
    {days_ago:N}             → N 天前的日期
    {days_ago:N:format}
    {weeks_ago:N}            → N 周前（N×7 天前）
    {weeks_ago:N:format}
    {this_week:N}            → N=1~7 本周一～周日
    {this_week:N:format}
    {last_week:N}            → N=1~7 上周一～周日
    {last_week:N:format}

  自定义变量:
    {task_name}              → 当前任务名
    {xxx}                    → 从 context 或 params 中查找

  环境变量:
    ${ENV_VAR}               → 环境变量值（缺失时保留原样并告警）
"""

import logging
import os
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 匹配 ${ENV_VAR}
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_env_vars(text: str | None) -> str | None:
    """替换字符串中的 ``${ENV_VAR}`` —— 全仓唯一实现。

    语义：命中环境变量则替换为它的值；环境变量缺失时**保留 ``${VAR}`` 原样**
    并记录 warning（不静默替换为空串）。

    注意：只处理 ``${...}``，普通 ``{xxx}`` 不会被当模板解析。
    像数据库密码这种字段应调用本函数而**不要**调用
    :meth:`URLTemplate.resolve`，以免密码里的 ``{xxx}`` 被误当模板。

    非字符串输入（如 YAML 里的 ``null``）原样返回。
    """
    if not isinstance(text, str):
        return text

    def env_replacer(match: re.Match) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None:
            logger.warning("环境变量未设置，保留原样: ${%s}", name)
            return match.group(0)
        return value

    return _ENV_PATTERN.sub(env_replacer, text)


class URLTemplate:
    """
    模板变量解析器（通用，不限于 URL）。

    使用方式:
        url = URLTemplate.resolve(
            "https://api.example.com/data?date={today}&from={yesterday}",
            context={"task_name": "xxx", "steam_id": 12345}
        )
    """

    # 匹配 {xxx} 或 {xxx:format} 或 {xxx:N} 或 {xxx:N:format}
    _VAR_PATTERN = re.compile(r"\{([a-z_0-9]+)(?::([^}]+))?\}", re.IGNORECASE)

    @staticmethod
    def resolve(template: str, context: dict = None) -> str:
        """
        替换模板字符串中的所有变量。

        参数:
            template: 含变量的模板字符串
            context: 额外上下文（如 task_name、iterate 变量、params）

        返回:
            替换后的字符串
        """
        if context is None:
            context = {}

        # 先替换 ${ENV}，再走 {var} 正则，避免 ${X} 被 {X} 规则误处理
        template = resolve_env_vars(template)

        now = datetime.now()

        def replacer(match: re.Match) -> str:
            var_name = match.group(1).lower()
            extra = match.group(2) or ""

            # ---- 时间类 ----

            # {today} / {today:format}
            if var_name == "today":
                return now.strftime(extra) if extra else now.strftime("%Y-%m-%d")

            # {yesterday} / {yesterday:format}
            if var_name == "yesterday":
                dt = now - timedelta(days=1)
                return dt.strftime(extra) if extra else dt.strftime("%Y-%m-%d")

            # {now} / {now:format}
            if var_name == "now":
                return now.strftime(extra) if extra else now.strftime("%Y-%m-%d %H:%M:%S")

            # {timestamp} 当前 Unix 秒级时间戳
            if var_name == "timestamp":
                return str(int(now.timestamp()))

            # {timestamp_ms} 当前 Unix 毫秒级时间戳
            if var_name == "timestamp_ms":
                return str(int(now.timestamp() * 1000))

            # {days_ago:N} / {days_ago:N:format}
            if var_name == "days_ago":
                parts = extra.split(":", 1)
                days = int(parts[0])
                fmt = parts[1] if len(parts) > 1 else "%Y-%m-%d"
                dt = now - timedelta(days=days)
                return dt.strftime(fmt)

            # {weeks_ago:N} / {weeks_ago:N:format}
            if var_name == "weeks_ago":
                parts = extra.split(":", 1)
                weeks = int(parts[0])
                fmt = parts[1] if len(parts) > 1 else "%Y-%m-%d"
                dt = now - timedelta(weeks=weeks)
                return dt.strftime(fmt)

            # {this_week:N} / {this_week:N:format}  N=1~7 本周一～周日
            if var_name == "this_week":
                parts = extra.split(":", 1)
                weekday = int(parts[0])  # 1=周一, 7=周日
                fmt = parts[1] if len(parts) > 1 else "%Y-%m-%d"
                # Python: Monday=0, Sunday=6
                # 用户: Monday=1, Sunday=7
                py_weekday = weekday - 1  # 转换为 Python weekday
                today_weekday = now.weekday()  # 0=周一
                delta_days = py_weekday - today_weekday
                dt = now + timedelta(days=delta_days)
                return dt.strftime(fmt)

            # {last_week:N} / {last_week:N:format}  N=1~7 上周一～周日
            if var_name == "last_week":
                parts = extra.split(":", 1)
                weekday = int(parts[0])  # 1=周一, 7=周日
                fmt = parts[1] if len(parts) > 1 else "%Y-%m-%d"
                py_weekday = weekday - 1
                today_weekday = now.weekday()
                # 先定位到本周该天，再减7天
                delta_days = py_weekday - today_weekday - 7
                dt = now + timedelta(days=delta_days)
                return dt.strftime(fmt)

            # ---- 自定义变量 ----

            # {task_name}
            if var_name == "task_name":
                return str(context.get("task_name", ""))

            # 从 context / params 中查找
            if var_name in context:
                return str(context[var_name])

            # 完全不认识的变量，保留原文
            return match.group(0)

        return URLTemplate._VAR_PATTERN.sub(replacer, template)