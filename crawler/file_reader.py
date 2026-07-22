"""
文件数据源模块：读取 CSV / Excel 文件，统一返回 list[dict]。

CSV 使用标准库 csv.DictReader（无额外依赖）。
Excel 使用 openpyxl（需 pip install openpyxl）。

使用方式:
    reader = FileReader()
    rows = reader.read({"format": "csv", "path": "data.csv", "encoding": "utf-8"})
"""

import csv
import logging
from typing import Any

logger = logging.getLogger(__name__)

# openpyxl 延迟导入，仅在使用 Excel 格式时才需要


class FileReader:
    """
    文件读取器，支持 CSV 和 Excel 两种格式。

    读取后的 list[dict] 可直接传给 parser（使用 sdk_mapping 类型透传）。
    """

    @staticmethod
    def read(file_config: dict) -> list[dict]:
        """
        根据文件配置读取数据。

        参数:
            file_config: YAML 中的 file 配置块
                {
                    "format": "csv" | "excel",
                    "path": "data/sample.csv",
                    "encoding": "utf-8",        # 可选，默认 utf-8
                    "delimiter": ",",            # 可选，CSV 专用，默认逗号
                    "sheet_name": "Sheet1",      # 可选，Excel 专用
                }

        返回:
            list[dict] - 每行一个 dict，键为列名
        """
        fmt = file_config.get("format", "").lower()
        path = file_config.get("path", "")

        if not path:
            raise ValueError("file 配置缺少 path")

        logger.info("读取文件: %s (format=%s)", path, fmt)

        if fmt == "excel":
            return FileReader._read_excel(file_config)
        else:
            # 默认按 CSV 处理
            return FileReader._read_csv(file_config)

    @staticmethod
    def _read_csv(file_config: dict) -> list[dict]:
        path = file_config.get("path", "")
        encoding = file_config.get("encoding", "utf-8-sig")
        delimiter = file_config.get("delimiter", ",")

        rows = []
        with open(path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                # 去除键值对中的空白
                cleaned = {k.strip(): v.strip() if isinstance(v, str) else v
                            for k, v in row.items()}
                rows.append(cleaned)

        logger.info("CSV 读取完成: %d 行, %d 列 (%s)", len(rows),
                     len(rows[0]) if rows else 0, path)
        return rows

    @staticmethod
    def _read_excel(file_config: dict) -> list[dict]:
        path = file_config.get("path", "")
        sheet_name = file_config.get("sheet_name", 0)  # 0 = 第一张表

        try:
            import openpyxl
        except ImportError:
            raise ImportError(
                "读取 Excel 文件需要安装 openpyxl:\n"
                "  pip install openpyxl"
            )

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        if isinstance(sheet_name, int):
            ws = wb.worksheets[sheet_name]
        else:
            ws = wb[sheet_name]

        rows_iter = ws.iter_rows(values_only=True)

        # 第一行作为表头
        try:
            headers = [str(h).strip() if h is not None else f"col_{i}"
                       for i, h in enumerate(next(rows_iter))]
        except StopIteration:
            logger.warning("Excel 文件为空: %s", path)
            return []

        rows = []
        for row_values in rows_iter:
            if all(v is None for v in row_values):
                continue  # 跳过全空行
            row_dict = {}
            for i, val in enumerate(row_values):
                if i < len(headers):
                    row_dict[headers[i]] = str(val).strip() if val is not None else ""
            rows.append(row_dict)

        wb.close()
        logger.info("Excel 读取完成: %d 行, %d 列, sheet=%s (%s)",
                     len(rows), len(headers), sheet_name, path)
        return rows