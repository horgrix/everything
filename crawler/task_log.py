"""
组级文件日志模块：把每个 group 的运行时日志写到 logs/<group>/ 下。

- data.log   — 响应 body 原文（仅当 logging.data 开启）
- runtime.log — 每次任务完成/失败的摘要
- error.log  — 错误信息 + 出错的 body（若有）

三者均为覆盖式写入（open 'w'）。data.log 只写响应 body 原文，绝不写 headers；
超过 5MB 截断并在开头写 "[TRUNCATED] 原始大小 X bytes"。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# data.log 截断阈值（5MB）
DATA_LIMIT_BYTES = 5 * 1024 * 1024


def resolve_log_config(task_config) -> dict:
    """从任务配置读取 logging 配置并应用默认值。

    兼容 dict 与 TaskConfig（二者都实现 .get()）。
    默认：enabled=True, error=True, runtime=True, data=False。
    """
    cfg = task_config.get("logging") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "data": bool(cfg.get("data", False)),
        "runtime": bool(cfg.get("runtime", True)),
        "error": bool(cfg.get("error", True)),
    }


def _serialize_body(body) -> str:
    """把 raw_data 序列化为文本（data.log / error.log 用）。"""
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(body)


class TaskLogHandler:
    """按 group 管理覆盖式文件日志。"""

    def __init__(self, group: str, base_dir: str = "logs"):
        self.group = group or "default"
        self.base_dir = Path(base_dir)

    # ── 路径 ──

    @property
    def dir(self) -> Path:
        return self.base_dir / self.group

    def _path(self, filename: str) -> Path:
        return self.dir / filename

    # ── 落盘（同步，供 asyncio.to_thread 调用） ──

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ── 三个日志方法 ──

    async def log_data(self, task_name: str, body) -> None:
        """覆盖写 data.log：只写响应 body 原文，>5MB 截断。"""
        text = _serialize_body(body)
        raw_size = len(text.encode("utf-8"))
        if raw_size > DATA_LIMIT_BYTES:
            text = (
                f"[TRUNCATED] 原始大小 {raw_size} bytes\n"
                + text.encode("utf-8")[:DATA_LIMIT_BYTES].decode("utf-8", "ignore")
            )
        await asyncio.to_thread(self._write, self._path("data.log"), text)

    async def log_runtime(self, msg: str) -> None:
        """覆盖写 runtime.log：运行时摘要。"""
        await asyncio.to_thread(self._write, self._path("runtime.log"), msg)

    async def log_error(self, msg: str, body=None) -> None:
        """覆盖写 error.log：错误信息 + 出错的 body（若有）。"""
        text = msg
        if body is not None:
            text += "\n\n--- body ---\n" + _serialize_body(body)
        await asyncio.to_thread(self._write, self._path("error.log"), text)
