"""
数据解析模块：将 HTML / JSON / SDK 响应解析为结构化 list[dict]。

支持的解析类型：
  - json          : JSON 数组/对象，支持 root_path 定位
  - css_selector  : HTML 单记录提取
  - html_table    : HTML 表格多行解析
  - sdk_mapping   : SDK 返回的 list[dict] 字段映射

核心入口：
  - parse()          → 单记录模式（dict）
  - parse_rows()     → 多记录模式（list[dict]），统一处理 JSON/HTML/SDK
"""

import logging
from typing import Any
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Parser:
    """
    数据解析器。

    使用方式:
        parser = Parser()
        # 单记录
        data = parser.parse(html_text, {"type": "css_selector", "fields": [...]})
        # 多记录
        rows = parser.parse_rows(json_text, {"type": "json", "root_path": "data.items", "fields": [...]})
    """

    # ================================================================
    # 公共入口
    # ================================================================

    def parse(self, raw_content: str, parser_config: dict, context: dict = None) -> dict:
        """单记录模式，返回单个 dict"""
        if context is None:
            context = {}
        fields = parser_config.get("fields", [])
        parser_type = parser_config.get("type", "json")

        if parser_type in ("html", "css_selector"):
            return self._parse_html_single(raw_content, fields, context)
        elif parser_type == "json":
            return self._parse_json_single(raw_content, fields, context)
        else:
            logger.warning("未知解析器类型: %s，回退为 JSON 解析", parser_type)
            return self._parse_json_single(raw_content, fields, context)

    def parse_rows(self, raw_content_or_data, parser_config: dict,
                   context: dict = None) -> list[dict]:
        """
        统一的数组解析入口。根据 parser.type 自动路由到 JSON / HTML / SDK 提取策略。

        返回:
            list[dict] - 解析并过滤后的记录列表
        """
        if context is None:
            context = {}

        parser_type = parser_config.get("type", "json")
        fields = parser_config.get("fields", [])

        # 1. 提取原始行数据
        if parser_type == "html_table":
            raw_rows = self._extract_html_table_rows(raw_content_or_data, parser_config)
        elif parser_type == "sdk_mapping":
            raw_rows = raw_content_or_data if isinstance(raw_content_or_data, list) else []
        else:  # json (default)
            raw_rows = self._extract_json_rows(raw_content_or_data, parser_config)

        # 2. 逐行逐字段提取
        results = []
        for row in raw_rows:
            mapped = {}
            for field in fields:
                mapped[field["name"]] = self._extract_field_value(
                    row, field, parser_config, context
                )
            results.append(mapped)
        
        # 3. 过滤
        return self._apply_filters(results, parser_config)

    def parse_sdk_mapping(self, data: list[dict], parser_config: dict,
                          context: dict = None) -> list[dict]:
        """向后兼容：直接委托给 parse_rows"""
        return self.parse_rows(data, {**parser_config, "type": "sdk_mapping"}, context)

    def parse_array(self, raw_content: str, parser_config: dict,
                    context: dict = None) -> list[dict]:
        """向后兼容：直接委托给 parse_rows"""
        return self.parse_rows(raw_content, parser_config, context)

    def parse_html_array(self, html: str, parser_config: dict,
                         context: dict = None) -> list[dict]:
        """向后兼容：直接委托给 parse_rows"""
        return self.parse_rows(html, {**parser_config, "type": "html_table"}, context)

    # ================================================================
    # 原始行提取器 —— 各 parser type 专有逻辑
    # ================================================================

    def _extract_json_rows(self, raw_content: str, parser_config: dict) -> list:
        """从 JSON 字符串中提取行数据"""
        import json
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error("JSON 解析失败: %s", e)
            return []

        root_path = parser_config.get("root_path", "")
        if root_path:
            try:
                records = self._get_nested_value(data, root_path)
            except (KeyError, IndexError, TypeError) as e:
                logger.error("无法定位 root_path '%s': %s", root_path, e)
                return []
        else:
            records = data

        if isinstance(records, dict):
            records = [records]
        elif not isinstance(records, list):
            logger.error("数据不是数组或对象，类型为 %s", type(records).__name__)
            return []

        return records

    def _extract_html_table_rows(self, html: str, parser_config: dict) -> list:
        """从 HTML 中按 row_selector 提取行元素"""
        row_selector = parser_config.get("row_selector", "")
        if not row_selector:
            logger.error("html_table 需要 row_selector 配置")
            return []

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select(row_selector)
        if not rows:
            logger.warning("row_selector '%s' 未匹配到任何行", row_selector)
        return rows  # 返回 BeautifulSoup 元素列表

    # ================================================================
    # 字段值提取器 —— 统一处理所有 field 配置
    # ================================================================

    def _extract_field_value(self, row, field: dict, parser_config: dict,
                              context: dict) -> Any:
        """
        从一行的原始数据中提取单个字段值。
        支持策略（按优先级）：value 占位符 > position 索引 > column+selector 组合 > path 路径 > source 映射
        """
        # 1. 静态值 / 占位符
        if "value" in field:
            return self._resolve_value(field["value"], context)

        parser_type = parser_config.get("type", "json")
        index_mapping = parser_config.get("array_index_mapping", False)

        # 2. JSON 二维数组：position 索引
        if index_mapping and parser_type != "html_table":
            pos = field.get("position")
            if pos is not None and isinstance(row, list) and pos < len(row):
                return row[pos]
            return None

        # 3. HTML 表格/元素提取
        if parser_type in ("html_table", "html", "css_selector"):
            return self._extract_html_field(row, field)

        # 4. dict 模式：path（json）或 source（sdk_mapping）
        if isinstance(row, dict):
            # source 映射优先
            source = field.get("source")
            if source and source in row:
                return row[source]
            # path 提取
            json_path = field.get("path") or field.get("selector")
            if json_path:
                try:
                    return self._get_nested_value(row, json_path)
                except (KeyError, IndexError, TypeError):
                    pass
        return None

    def _extract_html_field(self, element, field: dict) -> Any:
        """从 BeautifulSoup 元素中提取字段值"""
        col_index = field.get("column")
        selector = field.get("selector")

        # 有 column + selector：先定位 td，再提取子元素
        if col_index is not None:
            cells = element.select("td, th")
            if col_index >= len(cells):
                return None
            target = cells[col_index]
            if selector:
                elements = target.select(selector)
                if elements:
                    return self._get_element_value(elements[0], field)
                return None
            return self._get_element_value(target, field)

        # 仅 selector：在当前元素内查找
        if selector:
            elements = element.select(selector)
            if elements:
                if field.get("multiple"):
                    return [self._get_element_value(el, field) for el in elements]
                return self._get_element_value(elements[0], field)
            return None

        return self._get_element_value(element, field)

    @staticmethod
    def _post_process_value(value: Any, field: dict) -> Any:
        """对提取后的值立即应用 regex_extract + to_number，使 where 过滤可用"""
        if not isinstance(value, str):
            return value

        pattern = field.get("regex_extract")
        if pattern:
            import re
            m = re.search(pattern, value)
            value = m.group(1) if m and m.groups() else (m.group(0) if m else value)

        if field.get("to_number") and value:
            try:
                value = float(value)
                value = int(value) if value == int(value) else value
            except (ValueError, TypeError):
                pass

        return value

    @staticmethod
    def _get_element_value(element, field: dict) -> str:
        """从 BeautifulSoup 元素获取文本或属性值"""
        attr = field.get("attr")
        value = element.get(attr, "") if attr else element.get_text()
        if field.get("strip", True) and isinstance(value, str):
            value = value.strip()
        return value

    @staticmethod
    def _get_nested_value(data: Any, path: str) -> Any:
        """支持点号分隔的嵌套路径，如 "data.items.0.title" """
        current = data
        for key in path.split("."):
            if isinstance(current, list):
                key = int(key)
            current = current[key]
        return current

    # ================================================================
    # 单记录提取方法（向后兼容）
    # ================================================================

    def _parse_json_single(self, raw: str, fields: list[dict], context: dict) -> dict:
        import json
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("JSON 解析失败: %s", e)
            return {}

        result = {}
        for field in fields:
            name = field["name"]
            if "value" in field:
                result[name] = self._resolve_value(field["value"], context)
                continue
            json_path = field.get("path") or field.get("selector")
            if json_path:
                try:
                    result[name] = self._get_nested_value(data, json_path)
                except (KeyError, IndexError, TypeError):
                    result[name] = None
            else:
                result[name] = None
        return result

    def _parse_html_single(self, html: str, fields: list[dict], context: dict) -> dict:
        soup = BeautifulSoup(html, "lxml")
        result = {}
        for field in fields:
            name = field["name"]
            if "value" in field:
                result[name] = self._resolve_value(field["value"], context)
                continue
            selector = field.get("selector")
            if not selector:
                result[name] = None
                continue
            elements = soup.select(selector)
            if not elements:
                result[name] = None
            elif len(elements) == 1 or not field.get("multiple"):
                result[name] = self._get_element_value(elements[0], field)
            else:
                result[name] = [self._get_element_value(el, field) for el in elements]
        return result

    @staticmethod
    def _resolve_value(value: str, context: dict) -> str:
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return context.get(value[1:-1], value)
        return value
    
    @staticmethod
    def _apply_filters(rows: list[dict], parser_config: dict) -> list[dict]:
        filters = parser_config.get("filters", {})
        if not filters:
            return rows

        if (n := filters.get("skip_lines")) and n > 0:
            rows = rows[n:]
        if (n := filters.get("head")) and n > 0:
            rows = rows[:n]
        if (n := filters.get("tail")) and n > 0:
            rows = rows[-n:]

        return rows