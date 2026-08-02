"""
Typed configuration dataclasses for crawler tasks.

These provide type-safe access to YAML config without breaking
existing dict-based consumers. Each dataclass wraps the raw dict
and exposes typed properties; it also supports dict-style access
for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class _ConfigBase:
    """Mixin: dict-style read access for backward compatibility."""

    __slots__ = ("_raw",)

    def __init_subclass__(cls, **kwargs):
        # Make subclasses dataclasses automatically
        super().__init_subclass__(**kwargs)
        dataclass(cls)

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._raw.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._raw

    def keys(self):
        return self._raw.keys()

    def values(self):
        return self._raw.values()

    def items(self):
        return self._raw.items()

    def to_dict(self) -> dict:
        return dict(self._raw)


# ── Leaf configs ──────────────────────────────────────────────


class IterateVar(_ConfigBase):
    """Single iterate variable: var_name + values list."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "IterateVar":
        return cls(_raw=d)

    @property
    def var_name(self) -> str: return self._raw["var_name"]
    @property
    def values(self) -> list: return self._raw["values"]


class FieldConfig(_ConfigBase):
    """A single parser field definition with optional cleaning rules."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "FieldConfig":
        return cls(_raw=d)

    @property
    def name(self) -> str: return self._raw["name"]
    @property
    def path(self) -> str | None: return self._raw.get("path")
    @property
    def source(self) -> str | None: return self._raw.get("source")
    @property
    def column(self) -> int | None: return self._raw.get("column")
    @property
    def value(self) -> str | None: return self._raw.get("value")
    @property
    def position(self) -> int | None: return self._raw.get("position")
    @property
    def selector(self) -> str | None: return self._raw.get("selector")
    @property
    def attr(self) -> str | None: return self._raw.get("attr")
    @property
    def multiple(self) -> bool: return self._raw.get("multiple", False)
    @property
    def strip(self) -> bool: return self._raw.get("strip", True)
    @property
    def to_number(self) -> bool: return self._raw.get("to_number", False)
    @property
    def to_datetime(self) -> bool: return self._raw.get("to_datetime", False)


class ColumnDef(_ConfigBase):
    """Table column definition."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnDef":
        return cls(_raw=d)

    @property
    def name(self) -> str: return self._raw["name"]
    @property
    def type(self) -> str: return self._raw["type"]
    @property
    def constraint(self) -> str | None: return self._raw.get("constraint")


class IndexDef(_ConfigBase):
    """Table index definition."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "IndexDef":
        return cls(_raw=d)

    @property
    def name(self) -> str: return self._raw["name"]
    @property
    def columns(self) -> list[str]: return self._raw["columns"]
    @property
    def unique(self) -> bool: return self._raw.get("unique", False)


class TableSchema(_ConfigBase):
    """Table schema: columns + indexes."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "TableSchema":
        return cls(_raw=d)

    @property
    def columns(self) -> list[ColumnDef]:
        return [ColumnDef(_raw=c) for c in self._raw.get("columns", [])]

    @property
    def indexes(self) -> list[IndexDef]:
        return [IndexDef(_raw=i) for i in self._raw.get("indexes", [])]


class ParserConfig(_ConfigBase):
    """Parser configuration for one output target."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "ParserConfig":
        return cls(_raw=d)

    @property
    def type(self) -> str: return self._raw.get("type", "json")
    @property
    def fields(self) -> list[FieldConfig]:
        return [FieldConfig(_raw=f) for f in self._raw.get("fields", [])]

    @property
    def root_path(self) -> str | None: return self._raw.get("root_path")
    @property
    def row_selector(self) -> str | None: return self._raw.get("row_selector")
    @property
    def array_index_mapping(self) -> bool:
        return self._raw.get("array_index_mapping", False)
    @property
    def element_selector(self) -> dict: return self._raw.get("element_selector", {})
    @property
    def filters(self) -> dict: return self._raw.get("filters", {})


# ── Output / Task configs ─────────────────────────────────────


class OutputConfig(_ConfigBase):
    """One output target (target_table + schema + parser)."""
    _raw: dict

    @classmethod
    def from_dict(cls, d: dict) -> "OutputConfig":
        return cls(_raw=d)

    @property
    def target_table(self) -> str: return self._raw["target_table"]

    @property
    def table_schema(self) -> TableSchema:
        return TableSchema(_raw=self._raw.get("table_schema", {}))

    @property
    def parser(self) -> ParserConfig:
        return ParserConfig(_raw=self._raw.get("parser", {}))


@dataclass
class TaskConfig(_ConfigBase):
    """
    Top-level task configuration.

    Wraps the raw dict from YAML and adds typed property access.
    Still supports dict-style access (get, [], keys, items) for
    backward compatibility with existing code.
    """
    _raw: dict

    @classmethod
    def from_dict(cls, data: dict) -> "TaskConfig":
        """Parse a YAML-loaded dict into a TaskConfig."""
        return cls(_raw=data)

    # ── Core fields ──

    @property
    def name(self) -> str: return self._raw["name"]

    @property
    def type(self) -> str:
        """Task type: 'api', 'web', 'sdk', 'csv', 'excel', 'db'."""
        return self._raw.get("type", "web")

    @property
    def method(self) -> str: return self._raw.get("method", "GET")

    @property
    def url(self) -> str: return self._raw.get("url", "")

    @property
    def schedule(self) -> str | None: return self._raw.get("schedule")

    @property
    def trigger_type(self) -> str: return self._raw.get("trigger_type", "system")

    @property
    def enabled(self) -> bool: return self._raw.get("enabled", True)

    @property
    def encoding(self) -> str: return self._raw.get("encoding", "utf-8")

    # ── Sub-configs ──

    @property
    def outputs(self) -> list[OutputConfig]:
        return [OutputConfig(_raw=o) for o in self._raw.get("outputs", [])]

    @property
    def params(self) -> dict: return self._raw.get("params", {})

    @property
    def iterate(self) -> list[IterateVar]:
        raw = self._raw.get("iterate", {})
        if not raw:
            return []
        items = raw if isinstance(raw, list) else [raw]
        return [IterateVar(_raw=i) for i in items]

    @property
    def browser(self) -> dict: return self._raw.get("browser", {})

    @property
    def anti_spider(self) -> dict: return self._raw.get("anti_spider", {})

    @property
    def retry(self) -> dict: return self._raw.get("retry", {})

    @property
    def provider(self) -> dict: return self._raw.get("provider", {})

    @property
    def file(self) -> dict: return self._raw.get("file", {})

    @property
    def db(self) -> dict: return self._raw.get("db", {})

    # ── First output convenience ──

    @property
    def target_table(self) -> str:
        """Primary target table (first output's table)."""
        outputs = self.outputs
        return outputs[0].target_table if outputs else "unknown"
