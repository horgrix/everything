"""
File data source: reads CSV / Excel files into list[dict].

Handles task types: 'csv', 'excel'.

Uses asyncio.to_thread() for synchronous file I/O.
"""

import asyncio
import csv
import logging
from typing import Any

from .base import DataSource

logger = logging.getLogger(__name__)


class FileSource(DataSource):
    """
    File data source — reads local CSV / Excel files.

    File I/O is synchronous and runs via asyncio.to_thread().

    Returns list[dict] with column-name keys (first row as header).
    """

    # ---- DataSource interface ----

    async def fetch(self, task_config: dict, context: dict) -> list[dict]:
        """Read file in a thread, return list[dict]."""
        file_config = task_config.get("file", {})
        return await asyncio.to_thread(self._read_sync, file_config)

    # ---- Internal ----

    @staticmethod
    def _read_sync(file_config: dict) -> list[dict]:
        """Synchronous file read (runs in a thread)."""
        fmt = file_config.get("format", "").lower()
        path = file_config.get("path", "")

        if not path:
            raise ValueError("file config missing 'path'")

        logger.info("Reading file: %s (format=%s)", path, fmt)

        if fmt == "excel":
            return FileSource._read_excel(file_config)
        else:
            return FileSource._read_csv(file_config)

    @staticmethod
    def _read_csv(file_config: dict) -> list[dict]:
        path = file_config.get("path", "")
        encoding = file_config.get("encoding", "utf-8-sig")
        delimiter = file_config.get("delimiter", ",")

        rows = []
        with open(path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                cleaned = {
                    k.strip(): v.strip() if isinstance(v, str) else v
                    for k, v in row.items()
                }
                rows.append(cleaned)

        n_cols = len(rows[0]) if rows else 0
        logger.info("CSV read: %d rows, %d cols (%s)", len(rows), n_cols, path)
        return rows

    @staticmethod
    def _read_excel(file_config: dict) -> list[dict]:
        path = file_config.get("path", "")
        sheet_name = file_config.get("sheet_name", 0)

        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "Reading Excel requires openpyxl:\n"
                "  pip install openpyxl"
            )

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = (
            wb.worksheets[sheet_name]
            if isinstance(sheet_name, int)
            else wb[sheet_name]
        )

        rows_iter = ws.iter_rows(values_only=True)

        # First row = headers
        try:
            headers = [
                str(h).strip() if h is not None else f"col_{i}"
                for i, h in enumerate(next(rows_iter))
            ]
        except StopIteration:
            logger.warning("Excel file is empty: %s", path)
            wb.close()
            return []

        rows = []
        for row_values in rows_iter:
            if all(v is None for v in row_values):
                continue
            row_dict = {}
            for i, val in enumerate(row_values):
                if i < len(headers):
                    row_dict[headers[i]] = str(val).strip() if val is not None else ""
            rows.append(row_dict)

        wb.close()
        logger.info("Excel read: %d rows, %d cols, sheet=%s (%s)",
                     len(rows), len(headers), sheet_name, path)
        return rows
