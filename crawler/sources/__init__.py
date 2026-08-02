"""
Data source abstraction layer.

Each source implements the DataSource interface and is registered
by type string ('api', 'web', 'sdk', 'csv', 'excel', 'db') so the
Engine can route without if-else chains.
"""

from .base import DataSource, SourceRegistry
from .http_source import HttpSource
from .browser_source import BrowserSource
from .sdk_source import SdkSource
from .file_source import FileSource
from .db_source import DbSource


def create_default_registry() -> SourceRegistry:
    """Build a SourceRegistry with all built-in data sources."""
    browser = BrowserSource()
    registry = SourceRegistry()
    registry.register("api", HttpSource())
    registry.register("web", HttpSource(browser_source=browser))
    registry.register("sdk", SdkSource())
    registry.register("csv", FileSource())
    registry.register("excel", FileSource())
    registry.register("db", DbSource())
    return registry


__all__ = [
    "DataSource",
    "SourceRegistry",
    "HttpSource",
    "BrowserSource",
    "SdkSource",
    "FileSource",
    "DbSource",
    "create_default_registry",
]
