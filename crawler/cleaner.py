"""
Data cleaning module: transform parsed rows with pluggable rules.

All cleaning rules are registered in a phase-ordered registry instead of
hard-coded if-blocks.  Add a new rule by calling cleaner.register() —
no changes to clean_field() needed.

Built-in rules (phase 0 = text, phase 1 = type conversion, phase 2 = post):
  Phase 0: strip, truncate_left, truncate_right, trim_whitespace,
           remove_html, regex_extract, regex_replace
  Phase 1: number_expr_to_int, to_number, to_datetime
  Phase 2: ts_floor_to_hour, ts_floor_to_day
"""

import re
import logging
from html import unescape
from datetime import datetime
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Default date formats attempted by to_datetime
_DEFAULT_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日",
    "%b %d, %Y",
    "%B %d, %Y",
    "%B %Y",
    "%b %Y",
    "%Y-%m",
]

# ── Rule descriptor ─────────────────────────────────────────────


@dataclass
class CleanRule:
    """A single cleaning rule registered in the Cleaner."""

    key: str
    """The dict key in YAML field config that activates this rule."""

    apply: Callable[[Any, dict], Any] = field(repr=False)
    """(value, clean_rules) -> cleaned_value."""

    phase: int = 0
    """Execution phase: 0=text, 1=type conversion, 2=post-processing."""

    default_active: bool = False
    """
    If True, the rule fires even when its config key is absent
    (e.g. strip=True is the implicit default for all fields).
    """


# ── Cleaner ─────────────────────────────────────────────────────


class Cleaner:
    """
    Pluggable data cleaner.

    Rules are registered in a phase-ordered registry.  clean_field()
    iterates phases 0→2 and dispatches to matching rules — zero if-blocks.

    Usage:
        cleaner = Cleaner()
        cleaner.register("to_uppercase", phase=0,
                         func=lambda v, _: v.upper())
        cleaner.register_op("between",
                           lambda v, e: e[0] <= v <= e[1])
    """

    def __init__(self):
        self._rules: dict[str, CleanRule] = {}
        self._operators: dict[str, Callable[[Any, Any], bool]] = {}
        self._date_formats: list[str] = list(_DEFAULT_DATE_FORMATS)
        self._register_defaults()

    # ── Public API ───────────────────────────────────────────

    def register(
        self,
        key: str,
        phase: int,
        func: Callable[[Any, dict], Any],
        default_active: bool = False,
    ) -> None:
        """
        Register a custom cleaning rule.

        Args:
            key:            YAML config key that triggers the rule.
            phase:          0 (text), 1 (type conversion), or 2 (post).
            func:           (value, clean_rules) -> cleaned_value.
            default_active: If True, rule fires when key is absent from config.
        """
        self._rules[key] = CleanRule(
            key=key, phase=phase, apply=func, default_active=default_active,
        )

    def register_op(
        self,
        op: str,
        func: Callable[[Any, Any], bool],
    ) -> None:
        """
        Register a custom where-filter operator.

        Args:
            op:   Operator name used in YAML (e.g. "between").
            func: (actual_value, expected_value) -> bool.
        """
        self._operators[op] = func

    def register_date_format(self, fmt: str) -> None:
        """Append a date format to the to_datetime try-list."""
        if fmt not in self._date_formats:
            self._date_formats.append(fmt)

    # ── Batch entry points ────────────────────────────────────

    def clean(self, data: dict, parser_fields: list[dict]) -> dict:
        """Clean all fields in a single row dict."""
        field_config_map = {f["name"]: f for f in parser_fields}
        cleaned = {}
        for key, value in data.items():
            field_config = field_config_map.get(key, {})
            cleaned[key] = self.clean_field(value, field_config)
        return cleaned

    def should_keep(self, row: dict, parser_fields: list[dict]) -> bool:
        """Check if a cleaned row passes all where filters (AND)."""
        field_config_map = {f["name"]: f for f in parser_fields}
        for key, value in row.items():
            field_config = field_config_map.get(key, {})
            if not self._match_conditions(value, field_config):
                return False
        return True

    def clean_batch(
        self, rows: list[dict], parser_fields: list[dict]
    ) -> list[dict]:
        """Clean every row, keep only those that pass where filters."""
        results = []
        for row in rows:
            cleaned = self.clean(row, parser_fields)
            if self.should_keep(cleaned, parser_fields):
                results.append(cleaned)
        return results

    # ── Core: phase-driven rule dispatch ───────────────────────

    def clean_field(self, value: Any, field_config: dict) -> Any:
        """
        Clean a single field value by dispatching through the rule registry.

        Phases:
          0 — text transforms (strip, regex, truncate, …)
          1 — type conversions (to_number, to_datetime, …)
          2 — post-processing (ts_floor, …)

        No if-blocks — every rule is a registered pair of (config_key, func).
        """
        if value is None:
            return field_config.get("default")

        clean_rules = field_config.get("clean", field_config)
        if not isinstance(value, str):
            value = str(value)

        for phase in (0, 1, 2):
            for rule in self._rules.values():
                if rule.phase != phase:
                    continue
                # Dispatch decision: key is either present in config,
                # or absent-but-default-active (like strip).
                key_present = rule.key in clean_rules
                if not key_present and not rule.default_active:
                    continue
                value = rule.apply(value, clean_rules)

        return value

    # ── Where-condition dispatch ───────────────────────────────

    def _match_conditions(self, value: Any, field_config: dict) -> bool:
        """Dispatch where filter to the registered operator function."""
        filters: dict = field_config.get("where", {})
        if not filters:
            return True
        op = filters.get("op", "==")
        expected = filters.get("value")
        try:
            return self._operators.get(op, lambda a, b: True)(value, expected)
        except (TypeError, ValueError):
            return False

    # ── Built-in rule implementations ──────────────────────────

    @staticmethod
    def _rule_strip(value: Any, rules: dict) -> str:
        if not rules.get("strip", True):
            return value
        return value.strip()

    @staticmethod
    def _rule_truncate_left(value: Any, rules: dict) -> str:
        n = rules.get("truncate_left", 0)
        return value[:n] if n > 0 and len(value) > n else value

    @staticmethod
    def _rule_truncate_right(value: Any, rules: dict) -> str:
        n = rules.get("truncate_right", 0)
        return value[:-n] if n > 0 and len(value) > n else value

    @staticmethod
    def _rule_trim_whitespace(value: Any, _rules: dict) -> str:
        return re.sub(r"\s+", " ", value)

    @staticmethod
    def _rule_remove_html(value: Any, _rules: dict) -> str:
        text = re.sub(r"<[^>]+>", "", value)
        return unescape(text)

    @staticmethod
    def _rule_regex_extract(value: Any, rules: dict) -> str:
        pattern = rules["regex_extract"]
        group = rules.get("group", 1)
        match = re.search(pattern, value)
        if match:
            return match.group(group) if match.groups() else match.group(0)
        return rules.get("default", "")

    @staticmethod
    def _rule_regex_replace(value: Any, rules: dict) -> str:
        for rule in rules["regex_replace"]:
            value = re.sub(rule["pattern"], rule.get("replacement", ""), value)
        return value

    @staticmethod
    def _rule_number_expr_to_int(value: Any, _rules: dict) -> int:
        unit_map = {
            "十": 10, "百": 100, "千": 1000, "万": 10000,
            "M": 1_000_000, "亿": 100_000_000, "B": 1_000_000_000,
        }
        text = value.strip().replace(" ", "")
        m = re.match(r"^([\d.]+)\s*(.*)$", text)
        if not m:
            raise ValueError(f"Cannot parse: {text}")
        return int(float(m.group(1)) * unit_map.get(m.group(2), 1))

    @staticmethod
    def _rule_to_number(value: Any, _rules: dict) -> int | float | str:
        if not isinstance(value, str):
            return value
        text = value.strip().replace(",", "").replace("，", "")
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            logger.debug("Cannot convert '%s' to number", text)
            return text

    def _rule_to_datetime(self, value: Any, rules: dict) -> str:
        input_fmt = rules.get("date_format", "%Y-%m-%d %H:%M:%S")
        output_fmt = rules.get("date_output_format", "%Y-%m-%d %H:%M:%S")
        text = value.strip()
        for fmt in [input_fmt] + self._date_formats:
            try:
                return datetime.strptime(text, fmt).strftime(output_fmt)
            except (ValueError, TypeError):
                continue
        logger.debug("Cannot parse date: %s", text)
        return text

    @staticmethod
    def _rule_ts_floor_to_hour(value: Any, _rules: dict) -> int:
        _HOUR_MS = 3_600_000
        try:
            return value // _HOUR_MS * _HOUR_MS
        except (TypeError, ValueError):
            return value

    @staticmethod
    def _rule_ts_floor_to_day(value: Any, _rules: dict) -> int:
        _DAY_MS = 24 * 3_600_000
        try:
            return value // _DAY_MS * _DAY_MS
        except (TypeError, ValueError):
            return value

    # ── Built-in operator implementations ──────────────────────

    @staticmethod
    def _op_gt(v, e) -> bool:      return v is not None and v > e
    @staticmethod
    def _op_lt(v, e) -> bool:      return v is not None and v < e
    @staticmethod
    def _op_gte(v, e) -> bool:     return v is not None and v >= e
    @staticmethod
    def _op_lte(v, e) -> bool:     return v is not None and v <= e
    @staticmethod
    def _op_eq(v, e) -> bool:      return v == e
    @staticmethod
    def _op_ne(v, e) -> bool:      return v != e
    @staticmethod
    def _op_in(v, e) -> bool:      return v in e
    @staticmethod
    def _op_not_in(v, e) -> bool:  return v not in e
    @staticmethod
    def _op_contains(v, e) -> bool: return e in str(v)

    # ── Registration ───────────────────────────────────────────

    def _register_defaults(self) -> None:
        """Register all built-in rules and operators."""
        # --- phase 0: text transforms ---
        self.register("strip",             phase=0, func=self._rule_strip,
                      default_active=True)  # strip is on by default
        self.register("truncate_left",     phase=0, func=self._rule_truncate_left)
        self.register("truncate_right",    phase=0, func=self._rule_truncate_right)
        self.register("trim_whitespace",   phase=0, func=self._rule_trim_whitespace)
        self.register("remove_html",       phase=0, func=self._rule_remove_html)
        self.register("regex_extract",     phase=0, func=self._rule_regex_extract)
        self.register("regex_replace",     phase=0, func=self._rule_regex_replace)

        # --- phase 1: type conversions ---
        self.register("number_expr_to_int", phase=1, func=self._rule_number_expr_to_int)
        self.register("to_number",          phase=1, func=self._rule_to_number)
        self.register("to_datetime",        phase=1, func=self._rule_to_datetime)

        # --- phase 2: post-processing ---
        self.register("ts_floor_to_hour", phase=2, func=self._rule_ts_floor_to_hour)
        self.register("ts_floor_to_day",  phase=2, func=self._rule_ts_floor_to_day)

        # --- where operators ---
        for op, fn in [
            (">", self._op_gt), ("<", self._op_lt),
            (">=", self._op_gte), ("<=", self._op_lte),
            ("==", self._op_eq), ("!=", self._op_ne),
            ("in", self._op_in), ("not_in", self._op_not_in),
            ("contains", self._op_contains),
        ]:
            self.register_op(op, fn)

    # ── Utility ────────────────────────────────────────────────

    @staticmethod
    def field_names(fields: list[dict]) -> list[str]:
        """Extract field names from parser.fields config."""
        return [f["name"] for f in fields]
