"""
SDK data source: dynamic Python module import for third-party libraries.

Handles task type: 'sdk'.

Uses asyncio.to_thread() to avoid blocking the event loop during
synchronous SDK calls.
"""

import asyncio
import importlib
import logging
from typing import Any

from .base import DataSource

logger = logging.getLogger(__name__)


class SdkSource(DataSource):
    """
    SDK data source — calls third-party libraries like akshare/tushare.

    SDK calls are synchronous and potentially slow, so they run
    via asyncio.to_thread() to avoid blocking the event loop.

    Returns list[dict] (normalized from DataFrame/list/dict/scalar).
    """

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> list[dict]:
        """Execute SDK call in a thread, return normalized list[dict]."""
        provider_config = task_config.get("provider", {})
        return await asyncio.to_thread(self._call_sync, provider_config)

    # ---- Internal ----

    @staticmethod
    def _call_sync(provider_config: dict) -> list[dict]:
        """Synchronous SDK invocation (runs in a thread)."""
        module_name = provider_config.get("module", "")
        func_name = provider_config.get("function", "")
        params = provider_config.get("params", {})

        if not module_name or not func_name:
            raise ValueError("provider config missing 'module' or 'function'")

        # Dynamic import
        try:
            mod = importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(
                f"Cannot import SDK module '{module_name}'. "
                f"Install it with: pip install {module_name}"
            ) from e

        func = getattr(mod, func_name, None)
        if func is None:
            raise AttributeError(
                f"Module '{module_name}' has no function '{func_name}'"
            )

        logger.info("SDK call: %s.%s(%s)", module_name, func_name,
                     ", ".join(f"{k}={v}" for k, v in params.items()))

        try:
            result = func(**params)
        except Exception as e:
            logger.error("SDK call failed: %s.%s - %s", module_name, func_name, e)
            raise

        return SdkSource._normalize(result)

    @staticmethod
    def _normalize(result: Any) -> list[dict]:
        """Normalize SDK return value to list[dict]."""
        # pandas DataFrame
        if hasattr(result, "to_dict"):
            try:
                return result.to_dict(orient="records")
            except Exception:
                pass

        # Already list[dict]
        if isinstance(result, list):
            if len(result) == 0:
                return []
            if isinstance(result[0], dict):
                return result
            return [{"value": item} for item in result]

        # Single dict → wrap
        if isinstance(result, dict):
            return [result]

        # Scalar / None
        if result is None:
            return []
        return [{"value": result}]
