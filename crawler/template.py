"""
URL 模板变量解析模块。

支持在 YAML 配置的 url 字符串中使用运行时动态变量，
引擎在发起请求前自动替换。

支持的变量:
  {today}              → 2026-07-18
  {yesterday}          → 2026-07-17
  {now}                → 2026-07-18 20:30:00
  {now:format}         → 按 strftime 格式化当前时间
  {days_ago:N}         → N 天前的日期
  {days_ago:N:format}  → N 天前 + 自定义格式
  {task_name}          → 当前任务名
"""

import re
from datetime import datetime, timedelta


class URLTemplate:
    """
    URL 模板变量解析器。

    使用方式:
        url = URLTemplate.resolve(
            "https://api.example.com/data?date={today}&from={yesterday}",
            context={"task_name": "xxx"}
        )
    """

    # 匹配 {xxx} 或 {xxx:format} 或 {xxx:N} 或 {xxx:N:format}
    _VAR_PATTERN = re.compile(r"\{([a-z_]+)(?::([^}]+))?\}", re.IGNORECASE)

    @staticmethod
    def resolve(template: str, context: dict = None) -> str:
        """
        替换模板字符串中的所有变量。

        参数:
            template: 含变量的 URL 模板
            context: 额外上下文（如 task_name）

        返回:
            替换后的字符串
        """
        if context is None:
            context = {}

        now = datetime.now()

        def replacer(match: re.Match) -> str:
            var_name = match.group(1).lower()
            extra = match.group(2) or ""

            # {today} / {today:format}
            if var_name == "today":
                if extra:
                    return now.strftime(extra)
                return now.strftime("%Y-%m-%d")

            # {yesterday} / {yesterday:format}
            if var_name == "yesterday":
                dt = now - timedelta(days=1)
                if extra:
                    return dt.strftime(extra)
                return dt.strftime("%Y-%m-%d")

            # {now} / {now:format}
            if var_name == "now":
                if extra:
                    return now.strftime(extra)
                return now.strftime("%Y-%m-%d %H:%M:%S")

            # {days_ago:N} / {days_ago:N:format}
            if var_name == "days_ago":
                parts = extra.split(":", 1)
                days = int(parts[0])
                fmt = parts[1] if len(parts) > 1 else "%Y-%m-%d"
                dt = now - timedelta(days=days)
                return dt.strftime(fmt)

            # {task_name}
            if var_name == "task_name":
                return context.get("task_name", "")

            # 自定义变量：从 context 中查找
            if var_name in context:
                return str(context[var_name])
            # 完全不认识的变量，保留原文
            return match.group(0)

        return URLTemplate._VAR_PATTERN.sub(replacer, template)