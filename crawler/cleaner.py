"""
数据清洗模块：对解析后的结构化数据进行清理、转换。

支持的清洗操作（在 YAML parser.fields 中配置）：
  - strip       : 去除首尾空白
  - trim_whitespace : 压缩多余空白（多个空格→一个）
  - to_number   : 转换为数字（int/float）
  - to_datetime : 转换为标准时间格式
  - remove_html : 去除残留 HTML 标签
  - default     : 空值时使用默认值
  - regex_extract: 正则提取
  - regex_replace: 正则替换
"""

import re
import logging
from html import unescape
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class Cleaner:
    """
    数据清洗器。

    对解析后的每一个字段按配置的可选规则进行清理。

    使用方式:
        cleaner = Cleaner()
        cleaned = cleaner.clean_field("  Hello <b>World</b>  ", {
            "strip": True,
            "remove_html": True,
        })
    """

    def clean(self, data: dict, parser_fields: list[dict]) -> dict:
        """
        对解析结果中的所有字段依次清洗。

        参数:
            data: Parser 解析后的原始 dict
            parser_fields: YAML 中 parser.fields 配置（含清洗规则）

        返回:
            清洗后的 dict
        """
        cleaned = {}

        # 建立字段名→清洗配置的映射
        field_config_map = {f["name"]: f for f in parser_fields}

        for key, value in data.items():
            field_config = field_config_map.get(key, {})
            cleaned[key] = self.clean_field(value, field_config)

        return cleaned

    def clean_field(self, value: Any, field_config: dict) -> Any:
        """
        对单个字段值执行清洗。

        参数:
            value: 原始值
            field_config: 字段配置（可能包含 clean 子配置，或清洗规则直接在字段级）

        返回:
            清洗后的值
        """
        if value is None:
            default_val = field_config.get("default")
            return default_val

        # 清洗规则可以在 field_config.clean 子节点，也可以直接放在 field_config 上
        clean_rules = field_config.get("clean", field_config)

        # 转为字符串进行文本类处理
        if not isinstance(value, str):
            value = str(value)

        # --- 文本类清洗（仅对字符串有效） ---

        if clean_rules.get("strip", True):
            value = value.strip()

        # 字符串截断（在 strip 之后、其他操作之前执行）
        truncate_left = clean_rules.get("truncate_left")
        if truncate_left is not None and truncate_left > 0 and len(value) > truncate_left:
            value = value[:truncate_left]

        truncate_right = clean_rules.get("truncate_right")
        if truncate_right is not None and truncate_right > 0 and len(value) > truncate_right:
            value = value[-truncate_right:]

        if clean_rules.get("trim_whitespace"):
            value = re.sub(r"\s+", " ", value)

        if clean_rules.get("remove_html"):
            value = self._remove_html(value)

        if "regex_extract" in clean_rules:
            pattern = clean_rules["regex_extract"]
            match = re.search(pattern, value)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
            else:
                value = clean_rules.get("default", "")

        if "regex_replace" in clean_rules:
            for rule in clean_rules["regex_replace"]:
                pattern = rule["pattern"]
                replacement = rule.get("replacement", "")
                value = re.sub(pattern, replacement, value)

        # --- 类型转换（在文本清洗之后） ---

        if clean_rules.get("to_number") and value:
            value = self._to_number(value)

        if clean_rules.get("to_datetime") and value:
            value = self._to_datetime(value, clean_rules)

        return value

    # ================================================================
    # 清洗工具方法
    # ================================================================

    @staticmethod
    def _remove_html(text: str) -> str:
        """去除残留的 HTML 标签并用 html.unescape 解码实体"""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = unescape(clean)
        return clean

    @staticmethod
    def _to_number(text: str) -> int | float:
        """尝试将文本转为数字"""
        text = text.strip().replace(",", "").replace("，", "")
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            logger.debug("无法将 '%s' 转换为数字", text)
            return text

    @staticmethod
    def _to_datetime(text: str, clean_rules: dict) -> str:
        """尝试将各种日期格式统一为标准格式"""
        date_format = clean_rules.get("date_format", "%Y-%m-%d %H:%M:%S")
        output_fmt = clean_rules.get("date_output_format", "%Y-%m-%d %H:%M:%S")
        # 常见格式尝试
        formats = [
            date_format,
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y年%m月%d日 %H:%M:%S",
            "%Y年%m月%d日",
            "%b %d, %Y",
            "%B %d, %Y",
            "%B %Y",              # March 2026
            "%b %Y",              # Mar 2026
            "%Y-%m",              # 2026-03（已经是目标格式）
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(text.strip(), fmt)
                return dt.strftime(output_fmt)
            except (ValueError, TypeError):
                continue
        # 如果都解析不了，返回原文
        logger.debug("无法解析日期: %s", text)
        return text