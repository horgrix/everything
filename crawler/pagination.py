"""翻页支持：会话式分页（第一页拿 session_id，后续页带 offset + session 继续翻）。

有些推荐流接口不是无状态分页：第一页请求不带偏移，响应里给出一个
``session_id``（通常藏在 ``next_page`` 的 query 里），后续页必须带上同一个
``session_id`` 才会返回「同一路推荐流的第 N 条起」，否则服务器每次重新随机推荐、
翻页结果大量重叠。

任务配置示例：

    pagination:
      offset_param: from        # 偏移参数名，默认 from
      limit: 10                 # 每页条数，用来算第 N 页的 offset
      max_pages: 3              # 最多翻几页
      session_param: session_id # 会话参数名，默认 session_id
      session_from: "data.next_page"   # 第一页响应里取会话值的点分路径

引擎会把每页的 ``index_offset``（0、10、20…）写进 context，
parser 的 ``index`` 字段会自动叠加它，position 因此跨页连续。
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

__all__ = [
    "resolve_pagination",
    "extract_value",
    "extract_session",
    "build_page_url",
    "page_offset",
]


def resolve_pagination(task_config: dict) -> dict | None:
    """读取并规范化任务里的 pagination 配置；没配置或页数不足 2 时返回 None。"""
    raw = (task_config or {}).get("pagination") or {}
    if not raw:
        return None

    max_pages = _as_int(raw.get("max_pages", raw.get("pages"))) or 0
    limit = _as_int(raw.get("limit", raw.get("page_size"))) or 0
    if max_pages < 2 or limit < 1:
        return None

    return {
        "offset_param": str(raw.get("offset_param") or "from"),
        "limit": limit,
        "limit_param": str(raw.get("limit_param") or "limit"),
        "max_pages": max_pages,
        "session_param": str(raw.get("session_param") or "session_id"),
        "session_from": str(raw.get("session_from") or ""),
        "interval": float(raw.get("interval", 0) or 0),
    }


def page_offset(page: int, limit: int) -> int:
    """第 N 页（1-based）的偏移量：第 1 页 0，第 2 页 limit……"""
    return max(page - 1, 0) * limit


def extract_value(payload, dotted_path: str):
    """按点分路径从 payload（dict 或 JSON 字符串）里取值，取不到返回 None。"""
    if not dotted_path:
        return None

    data = payload
    if isinstance(data, (str, bytes, bytearray)):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return None

    cur = data
    for part in dotted_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
        if cur is None:
            return None
    return cur


def extract_session(payload, dotted_path: str, session_param: str) -> str | None:
    """从响应里取出会话值。

    ``dotted_path`` 指向的值可能是：
      - 直接的会话字符串（如 ``data.session_id``）；
      - 一个 URL（如 ``data.next_page``），此时取其中 ``session_param`` 查询参数。
    """
    value = extract_value(payload, dotted_path)
    if not isinstance(value, str) or not value:
        return None
    if "://" not in value and "?" not in value:
        return value

    query = parse_qs(urlparse(value).query)
    found = query.get(session_param)
    return found[0] if found else None


def build_page_url(base_url: str, params: dict) -> str:
    """在 base_url 上追加/覆盖查询参数（同名的会被替换，不重复）。"""
    parsed = urlparse(base_url)
    query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    for key, value in params.items():
        if value is None or value == "":
            query[key] = ""
        else:
            query[key] = str(value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _as_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
