"""
Data parser module: structured data extraction via pluggable strategies.

Parser types (row extraction strategies) and field extraction strategies
are registered in dicts/lists — no if-else chains.  Add a new parser type
or field strategy by calling register_*() methods.

Built-in row extractors:   json, html_table, sdk_mapping
Built-in field strategies: value placeholder, position index, HTML element, dict path/source
Built-in filters:          skip_lines, head, tail
"""

import logging
import json as _json
from typing import Any
from collections.abc import Callable

from bs4 import BeautifulSoup

from .template import URLTemplate

logger = logging.getLogger(__name__)


# ── Type aliases ──────────────────────────────────────────────

RowExtractor = Callable[[Any, dict], list]
"""raw_data + parser_config → list of raw rows"""

FieldCondition = Callable[[dict, dict], bool]
"""field_config + parser_config → True if this extractor applies"""

FieldExtractor = Callable[[Any, dict, dict, dict], Any]
"""raw_row + field_config + parser_config + context → extracted value"""

FilterFn = Callable[[list[dict], int], list[dict]]
"""rows + param_value → filtered rows"""


class Parser:
    """
    Pluggable data parser with registry-based dispatch.

    Usage:
        parser = Parser()

        # Register a custom row extractor
        parser.register_row_extractor(
            "xml", lambda raw, cfg: parse_xml_rows(raw)
        )

        # Register a custom field extractor (highest priority)
        parser.register_field_extractor(
            condition=lambda f, p: f.get("encrypted"),
            extractor=lambda r, f, p, c: decrypt(r[f["source"]])
        )

        # Register a custom filter
        parser.register_filter("sample", lambda rows, n: random.sample(rows, n))
    """

    def __init__(self):
        self._row_extractors: dict[str, RowExtractor] = {}
        self._field_strategies: list[tuple[FieldCondition, FieldExtractor]] = []
        self._filters: list[tuple[str, FilterFn]] = []
        self._operators: dict[str, Callable[[Any, Any], bool]] = {}
        self._register_defaults()

    # ── Public registration API ────────────────────────────────

    def register_row_extractor(
        self,
        parser_type: str,
        extractor: RowExtractor,
    ) -> None:
        """
        Register a row extraction strategy for a parser type string.

        Args:
            parser_type: Value of parser.type in YAML ("json", "html_table", …).
            extractor:   (raw_data, parser_config) -> list of raw rows.
        """
        self._row_extractors[parser_type] = extractor

    def register_field_extractor(
        self,
        condition: FieldCondition,
        extractor: FieldExtractor,
    ) -> None:
        """
        Append a field extraction strategy (appended last = lowest priority).

        Args:
            condition: (field_config, parser_config) -> True if applicable.
            extractor: (raw_row, field_config, parser_config, context) -> value.
        """
        self._field_strategies.append((condition, extractor))

    def register_field_extractor_first(
        self,
        condition: FieldCondition,
        extractor: FieldExtractor,
    ) -> None:
        """
        Prepend a field extraction strategy (prepended first = highest priority).
        """
        self._field_strategies.insert(0, (condition, extractor))

    def register_filter(
        self,
        param_name: str,
        filter_fn: FilterFn,
    ) -> None:
        """
        Register a parser-level filter.

        Args:
            param_name: The key in parser.filters dict that activates this filter.
            filter_fn:  (rows, param_value) -> filtered rows.
        """
        self._filters.append((param_name, filter_fn))

    def register_operator(
        self,
        op: str,
        func: Callable[[Any, Any], bool],
    ) -> None:
        """
        Register a when-condition operator.

        Args:
            op:   Operator name used in YAML when branch (e.g. "==", "in").
            func: (actual_value, expected_value) -> bool.
        """
        self._operators[op] = func

    # ── Public entry points ────────────────────────────────────

    def parse(
        self,
        raw_content: str,
        parser_config: dict,
        context: dict = None,
    ) -> dict:
        """Single-record mode: returns first row or {}."""
        rows = self.parse_rows(raw_content, parser_config, context)
        return rows[0] if rows else {}

    def parse_rows(
        self,
        raw_content_or_data,
        parser_config: dict,
        context: dict = None,
    ) -> list[dict]:
        """
        Parse raw data into rows using registry dispatch.

        Steps:
          1. Dispatch to the matching row extractor (no if-else).
          2. Extract every field via the strategy chain.
          3. Apply registered filters.
        """
        if context is None:
            context = {}

        parser_type = parser_config.get("type", "json")
        fields = parser_config.get("fields", [])

        # 1. Row extraction — dict lookup, zero if-blocks
        extractor = self._row_extractors.get(
            parser_type, self._extract_json_rows
        )
        raw_rows = extractor(raw_content_or_data, parser_config)

        # 2. Field extraction — strategy chain
        results = []
        for index, row in enumerate(raw_rows):
            # 注入遍历索引，供字段的 index 属性或 value: "{index}" 引用
            row_context = dict(context)
            row_context["index"] = index
            mapped = {}
            for field in fields:
                mapped[field["name"]] = self._extract_field_value(
                    row, field, parser_config, row_context
                )
            results.append(mapped)

        # 3. Filters
        return self._apply_filters(results, parser_config)

    def extract_element_vars(
        self,
        raw_html: str,
        element_selector_config: dict,
    ) -> dict:
        """
        Extract page-level variables from HTML via CSS selectors.
        Values are returned raw (no cleaning — cleaning happens in DataPipeline).
        """
        if not element_selector_config or not raw_html:
            return {}

        soup = BeautifulSoup(raw_html, "lxml")
        result = {}

        for var_name, var_config in element_selector_config.items():
            if not isinstance(var_config, dict):
                result[var_name] = var_config
                continue

            selector = var_config.get("selector", "")
            if not selector:
                logger.warning(
                    "element_selector '%s' is missing 'selector'", var_name
                )
                continue

            elements = soup.select(selector)
            if not elements:
                logger.warning(
                    "element_selector '%s' selector='%s' matched nothing",
                    var_name, selector,
                )
                result[var_name] = ""
                continue

            element = elements[0]
            attr = var_config.get("attr")
            result[var_name] = (
                element.get(attr, "") if attr else element.get_text()
            )

        logger.debug("element_selector result: %s", result)
        return result

    # ── Strategy-chain field extraction ────────────────────────

    def _extract_field_value(
        self,
        row: Any,
        field: dict,
        parser_config: dict,
        context: dict,
    ) -> Any:
        """
        Walk the field strategy chain and return the first non-None value.
        No if-else — just iterate over (condition, extractor) pairs.
        """
        for condition, extractor in self._field_strategies:
            if condition(field, parser_config):
                value = extractor(row, field, parser_config, context)
                if value is not None:
                    return value
        return None

    # ── Filter dispatch ────────────────────────────────────────

    def _apply_filters(
        self, rows: list[dict], parser_config: dict
    ) -> list[dict]:
        """Apply every registered filter parameter present in config."""
        filter_config = parser_config.get("filters", {})
        for param_name, filter_fn in self._filters:
            n = filter_config.get(param_name)
            if n and n > 0:
                rows = filter_fn(rows, n)
        return rows

    # ================================================================
    # Default row extractor implementations
    # ================================================================

    @staticmethod
    def _extract_json_rows(raw_content: str, parser_config: dict) -> list:
        """Parse JSON and navigate to root_path."""
        try:
            data = _json.loads(raw_content)
        except _json.JSONDecodeError as e:
            logger.error("JSON parse failed: %s", e)
            return []

        root_path = parser_config.get("root_path", "")
        if root_path:
            try:
                records = Parser._get_nested_value(data, root_path)
            except (KeyError, IndexError, TypeError) as e:
                logger.error("Cannot resolve root_path '%s': %s", root_path, e)
                return []
        else:
            records = data

        if isinstance(records, dict):
            return [records]
        if not isinstance(records, list):
            logger.error("Data is not a list or dict, got %s", type(records).__name__)
            return []
        return records

    @staticmethod
    def _extract_html_table_rows(html: str, parser_config: dict) -> list:
        """Extract <tr> elements matching row_selector."""
        row_selector = parser_config.get("row_selector", "")
        if not row_selector:
            logger.error("html_table requires 'row_selector'")
            return []

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select(row_selector)
        if not rows:
            logger.warning("row_selector '%s' matched nothing", row_selector)
        return rows  # BeautifulSoup elements

    @staticmethod
    def _passthrough_list(data, _parser_config: dict) -> list:
        """Pass through data that is already a list (sdk_mapping)."""
        return data if isinstance(data, list) else []

    # ================================================================
    # Default field extraction implementations
    # ================================================================

    @staticmethod
    def _cond_has_value(field: dict, _parser_config: dict) -> bool:
        return "value" in field

    @staticmethod
    def _extract_value(_row, field: dict, _pc: dict, context: dict) -> str:
        val = field["value"]
        if isinstance(val, str):
            # 统一走模板解析，支持 {var}、{now:format}、{today} 等时间/上下文变量
            return URLTemplate.resolve(val, context=context)
        return val

    @staticmethod
    def _cond_has_index(field: dict, _parser_config: dict) -> bool:
        return field.get("index") is not None

    @staticmethod
    def _extract_index(_row, field: dict, _pc: dict, context: dict) -> int:
        """返回当前遍历索引（0-based）+ index 属性的偏移。

        - index: true  → 等价于 index: 0（0, 1, 2, …）
        - index: 0     → 0-based 列表位置
        - index: 1     → 1-based 榜单排名（1, 2, 3, …）
        """
        base = field.get("index")
        if base is True:
            base = 0
        index = context.get("index", 0)
        try:
            return int(index) + int(base)
        except (TypeError, ValueError):
            return int(index)

    @staticmethod
    def _cond_position_index(field: dict, parser_config: dict) -> bool:
        return bool(
            parser_config.get("array_index_mapping")
            and field.get("position") is not None
            and parser_config.get("type") != "html_table"
        )

    @staticmethod
    def _extract_position(row, field: dict, _pc, _ctx) -> Any:
        if isinstance(row, list) and field["position"] < len(row):
            return row[field["position"]]
        return None

    @staticmethod
    def _cond_is_html(field: dict, parser_config: dict) -> bool:
        return parser_config.get("type") in ("html_table", "html", "css_selector")

    @staticmethod
    def _extract_html_field(row, field: dict, _pc, _ctx) -> Any:
        col_index = field.get("column")
        selector = field.get("selector")

        # column specified → locate td/th first
        if col_index is not None:
            cells = row.select("td, th")
            if col_index >= len(cells):
                return None
            target = cells[col_index]
            if selector:
                els = target.select(selector)
                return Parser._get_element_value(els[0], field) if els else None
            return Parser._get_element_value(target, field)

        # selector only → search within element
        if selector:
            els = row.select(selector)
            if els:
                if field.get("multiple"):
                    return [Parser._get_element_value(el, field) for el in els]
                return Parser._get_element_value(els[0], field)
            return None

        return Parser._get_element_value(row, field)

    @staticmethod
    def _get_element_value(element, field: dict) -> str:
        attr = field.get("attr")
        value = element.get(attr, "") if attr else element.get_text()
        if field.get("strip", True) and isinstance(value, str):
            value = value.strip()
        return value

    @staticmethod
    def _cond_is_dict(field: dict, _parser_config: dict) -> bool:
        return True  # fallthrough: handles JSON path + SDK source mapping

    @staticmethod
    def _extract_dict_field(row, field: dict, _pc, _ctx) -> Any:
        if not isinstance(row, dict):
            return None
        # source mapping takes priority
        source = field.get("source")
        if source and source in row:
            return row[source]
        # path-based extraction
        json_path = field.get("path") or field.get("selector")
        if json_path:
            try:
                return Parser._get_nested_value(row, json_path)
            except (KeyError, IndexError, TypeError):
                pass
        return None

    # ================================================================
    # when-condition field extraction (runtime branching)
    # ================================================================

    @staticmethod
    def _cond_has_when(field: dict, _parser_config: dict) -> bool:
        return "when" in field

    def _extract_when(self, row, field: dict, pc: dict, ctx: dict) -> Any:
        """按运行时条件选择提取逻辑：遍历 when 分支，匹配则用 then 提取。

        then / otherwise 是内嵌字段配置，递归复用策略链（支持嵌套 when）。
        无匹配且无 otherwise 时返回 None，让策略链继续降级到 field 自身的
        其他提取键（path/source/value 等）。
        """
        for branch in field.get("when", []):
            if not isinstance(branch, dict):
                continue
            if self._branch_matches(row, branch, ctx):
                then = branch.get("then")
                if isinstance(then, dict):
                    return self._extract_field_value(row, then, pc, ctx)
        otherwise = field.get("otherwise")
        if isinstance(otherwise, dict):
            return self._extract_field_value(row, otherwise, pc, ctx)
        return None

    def _branch_matches(self, row, branch: dict, ctx: dict) -> bool:
        # 注意：不能用 `on` 作为键名——它是 YAML 1.1 布尔保留字（会解析成 True）。
        # 主键为 `field`，同时兼容加引号的 `"on"`。
        field_name = branch.get("field", branch.get("on"))
        op = branch.get("op", "==")
        expected = branch.get("value")
        actual = Parser._resolve_when_value(row, field_name, ctx)
        expected = Parser._resolve_when_expected(expected, ctx)
        return self._match_op(actual, op, expected)

    @staticmethod
    def _resolve_when_value(row, field_name, ctx: dict) -> Any:
        """取条件判断值：field 优先作为当前行的点分 path，取不到回退到 context 变量。"""
        if isinstance(field_name, str):
            try:
                return Parser._get_nested_value(row, field_name)
            except (KeyError, IndexError, TypeError):
                return ctx.get(field_name)
        return field_name

    @staticmethod
    def _resolve_when_expected(value, ctx: dict) -> Any:
        """期望值支持模板变量（如 {today}、{app_id}）。"""
        if isinstance(value, str):
            return URLTemplate.resolve(value, context=ctx)
        return value

    def _match_op(self, actual, op: str, expected) -> bool:
        func = self._operators.get(op)
        if func is None:
            logger.warning("Unknown when operator: %s", op)
            return False
        try:
            return func(actual, expected)
        except (TypeError, ValueError):
            return False

    # ================================================================
    # Default filter implementations
    # ================================================================

    @staticmethod
    def _filter_skip_lines(rows: list[dict], n: int) -> list[dict]:
        return rows[n:]

    @staticmethod
    def _filter_head(rows: list[dict], n: int) -> list[dict]:
        return rows[:n]

    @staticmethod
    def _filter_tail(rows: list[dict], n: int) -> list[dict]:
        return rows[-n:]

    # ================================================================
    # Utility
    # ================================================================

    @staticmethod
    def _get_nested_value(data: Any, path: str) -> Any:
        """Resolve dotted path like 'data.items.0.title'."""
        current = data
        for key in path.split("."):
            if isinstance(current, list):
                key = int(key)
            current = current[key]
        return current

    # ================================================================
    # Registration
    # ================================================================

    def _register_defaults(self) -> None:
        """Wire up all built-in extractors and filters."""
        # Row extractors — keyed by parser.type
        self.register_row_extractor("json", self._extract_json_rows)
        self.register_row_extractor("html_table", self._extract_html_table_rows)
        self.register_row_extractor("css_selector", self._extract_html_table_rows)
        self.register_row_extractor("html", self._extract_html_table_rows)
        self.register_row_extractor("sdk_mapping", self._passthrough_list)

        # Field strategies — ordered from highest to lowest priority
        self.register_field_extractor_first(self._cond_has_when, self._extract_when)
        self.register_field_extractor(self._cond_has_value, self._extract_value)
        self.register_field_extractor(self._cond_has_index, self._extract_index)
        self.register_field_extractor(self._cond_position_index, self._extract_position)
        self.register_field_extractor(self._cond_is_html, self._extract_html_field)
        self.register_field_extractor(self._cond_is_dict, self._extract_dict_field)

        # Filters
        self.register_filter("skip_lines", self._filter_skip_lines)
        self.register_filter("head", self._filter_head)
        self.register_filter("tail", self._filter_tail)

        # when-condition operators
        for op, fn in [
            ("==", lambda a, e: a == e),
            ("!=", lambda a, e: a != e),
            ("in", lambda a, e: a in e),
            ("not_in", lambda a, e: a not in e),
            (">", lambda a, e: a > e),
            ("<", lambda a, e: a < e),
            (">=", lambda a, e: a >= e),
            ("<=", lambda a, e: a <= e),
            ("contains", lambda a, e: str(e) in str(a)),
        ]:
            self.register_operator(op, fn)
