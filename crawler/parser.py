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
        for row in raw_rows:
            mapped = {}
            for field in fields:
                mapped[field["name"]] = self._extract_field_value(
                    row, field, parser_config, context
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
        if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
            return context.get(val[1:-1], val)
        return val

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
        self.register_field_extractor(self._cond_has_value, self._extract_value)
        self.register_field_extractor(self._cond_position_index, self._extract_position)
        self.register_field_extractor(self._cond_is_html, self._extract_html_field)
        self.register_field_extractor(self._cond_is_dict, self._extract_dict_field)

        # Filters
        self.register_filter("skip_lines", self._filter_skip_lines)
        self.register_filter("head", self._filter_head)
        self.register_filter("tail", self._filter_tail)
