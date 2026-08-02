"""
Data source base protocol and registry.

All data sources implement a common interface so the engine can
fetch data without knowing the concrete implementation.
"""

from abc import ABC, abstractmethod
from typing import Any


class DataSource(ABC):
    """
    Abstract base for all data sources.

    Each implementation handles one or more task types (e.g. HttpSource
    handles 'api' and 'web'; FileSource handles 'csv' and 'excel').

    Usage:
        registry = SourceRegistry()
        registry.register("api", HttpSource())
        registry.register("sdk", SdkSource())
        source = registry.get(task_config["type"])
        raw_data = await source.fetch(task_config, context)
    """

    @abstractmethod
    async def fetch(self, task_config: dict, context: dict) -> Any:
        """
        Fetch raw data for a given task.

        Args:
            task_config: Full task configuration dict (from YAML).
            context: Resolved context dict (url, iterate vars, params, etc.).

        Returns:
            Raw data in a format consumable by the parser (str, list[dict], etc.).
            Return None to signal that this fetch should be skipped (e.g. URL dedup hit).
        """
        ...


class SourceRegistry:
    """
    Maps task type strings to DataSource instances.

    Multiple type strings can map to the same DataSource instance
    (e.g. 'api' and 'web' both use HttpSource).
    """

    def __init__(self):
        self._sources: dict[str, DataSource] = {}

    def register(self, source_type: str, source: DataSource) -> None:
        """Register a DataSource for a given task type string."""
        self._sources[source_type] = source

    def get(self, source_type: str) -> DataSource:
        """
        Look up the DataSource for a task type.

        Raises:
            ValueError: If no source is registered for this type.
        """
        if source_type not in self._sources:
            raise ValueError(
                f"Unknown source type: '{source_type}'. "
                f"Available: {sorted(self._sources.keys())}"
            )
        return self._sources[source_type]

    def get_all_types(self) -> list[str]:
        """Return all registered source type strings."""
        return sorted(self._sources.keys())
