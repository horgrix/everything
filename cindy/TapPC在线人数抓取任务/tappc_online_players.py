"""循环抓取 TapPC 各游戏在线人数并写入 crawler.db 的 taptap_pc_online_players 表。

流程：读取当天 app_id → 逐个调用 online-players-count（间隔 2 秒）→
解析返回 data.total 后写入目标表；crawled_at 取当前小时（本地时间），
按唯一索引 (crawled_at, app_id) 去重。
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
import urllib3

# 本地抓包环境未装证书，忽略 SSL 校验告警
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "crawler.db"
TABLE = "taptap_pc_hot_list_ids_hourly"
TARGET_TABLE = "taptap_pc_online_players"

REQUEST_INTERVAL = 1  # 每次请求间隔（秒）

def fetch_app_ids() -> list[int]:
    """查询表中 crawled_at 等于当前日期（本地时间）的数据，对 app_id 去重后升序返回。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT app_id FROM {TABLE} "
            "WHERE app_id IS NOT NULL "
            "AND crawled_at = strftime('%Y-%m-%d', 'now', 'localtime') "
            "ORDER BY app_id"
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows]

# 登录后拿到的凭证
KID = "whDzJuEy8c5RLtCHasHRIQniCZfaTlkFPoMHZ9Xx"
MAC_KEY = "EsDzJuEyrun4B3IgerRaUDdJFR1twHIIn5niTAex"
ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsInYiOjF9.eyJhdWQiOiJ3dTFqZWNjeHpxdmlsZGpvaGoiLCJqdGkiOiJ3aER6SnVFeThjNVJMdENIYXNIUklRbmlDWmZhVGxrRlBvTUhaOVh4IiwiaWF0IjoxNzkwMjAzMjYwLCJpc3MiOiJvYXV0aDI6VXNlciIsInN1YiI6IjY4OTY2NzY2NyJ9.jNlJg5WMHbhrjTyIZ8QAIL8ugNKlixX1smW8GLrRRayHgdi9SRCmKjcu1-q1tnHURvfiqOBLUtfk5bdUs6FiD84x6EU91D3j3OCR3qy6O7tdrbNzOJoJCsR50cae3PW_fWYnHbgkP7SCOFHFZQi7PSZVpkjlqGcOJpvzQiF4VX4"

HOST = "api.taptapdada.com"
PORT = "443"

def build_authorization(method, uri_with_query):
    ts = str(int(time.time()))                    # 秒级，10 位
    nonce = base64.b64encode(os.urandom(6)).decode()  # 8 位 Base64

    # 签名原文，格式是：ts\nnonce\nmethod\nuri\nhost\nport\n\n
    sign_str = f"{ts}\n{nonce}\n{method}\n{uri_with_query}\n{HOST}\n{PORT}\n\n"

    mac = base64.b64encode(
        hmac.new(MAC_KEY.encode(), sign_str.encode(), hashlib.sha1).digest()
    ).decode()

    return f'MAC id="{KID}",ts="{ts}",nonce="{nonce}",mac="{mac}"'

PATH = "/group/v1/online-players-count"
# 抓包得到的 X-UA 参数；app_id 由循环动态填充。
QUERY_TEMPLATE = (
    "?X-UA=V%3D1%26PN%3DTapPC%26VN%3D2026.9.22-rel.2%26VN_CODE%3D2026092202"
    "%26CH%3Drep-rep_xjkzxfe8mjr--260404zncardT541F2%26OS%3Dwindows"
    "%26OSV%3D10.0.26200%26LANG%3Dzh_CN%26UID%3De9b0bab39d9f4473ac1fd64288376937"
    "%26SR%3D3840x2160%26DEB%3DLENOVO%26DEM%3D90XF0014CP%26NT%3D7%26VID%3D689667667"
    "&app_id={app_id}"
)

HEADERS_TEMPLATE = {
    "user-agent": "TapTap/2026.9.22-rel.2 (Build 2026092202/50f6c0c7) TapPC-Main/2026.9.22-rel.2",
    "x-smfp": "m02cac8d13cf3dee9bafb1d5ded33f6963",
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip",
}


def fetch_online_players(app_id: int) -> int | None:
    """查询单个 app_id 的在线人数，返回 data.total；失败返回 None。"""
    query = QUERY_TEMPLATE.format(app_id=app_id)
    uri = PATH + query

    headers = dict(HEADERS_TEMPLATE)
    headers["authorization"] = build_authorization("GET", uri)

    url = f"https://{HOST}{uri}"

    try:
        resp = requests.get(url, headers=headers, verify=False, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        print(f"app_id={app_id} 请求失败：{exc}")
        return None

    if not payload.get("success"):
        print(f"app_id={app_id} 接口返回失败：{payload}")
        return None

    total = payload.get("data", {}).get("total")
    if total is None:
        print(f"app_id={app_id} 返回缺少 data.total：{payload}")
        return None

    return int(total)


def save_to_db(records: list[tuple[int, int]]) -> int:
    """把 (app_id, online_players) 批量写入目标表，crawled_at 取当前小时（本地时间）。

    按唯一索引 (crawled_at, app_id) 去重，重复记录被忽略；返回实际插入的行数。
    """
    if not records:
        return 0

    conn = sqlite3.connect(DB_PATH)
    try:
        before = conn.total_changes
        conn.executemany(
            f"INSERT OR IGNORE INTO {TARGET_TABLE} "
            "(app_id, online_players, crawled_at) "
            "VALUES (?, ?, strftime('%Y-%m-%d %H', 'now', 'localtime'))",
            records,
        )
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def main() -> int:
    # 确保 Windows 控制台以 UTF-8 输出，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    app_ids = fetch_app_ids()
    if not app_ids:
        print(f"表 '{TABLE}' 中当前日期没有 app_id，无需抓取")
        return 0

    print(f"待抓取 app_id 数量：{len(app_ids)}")

    records: list[tuple[int, int]] = []
    total_count = len(app_ids)
    for index, app_id in enumerate(app_ids, start=1):
        total = fetch_online_players(app_id)
        if total is None:
            print(f"[{index}/{total_count}] app_id={app_id} 抓取失败，跳过")
        else:
            records.append((app_id, total))
            print(f"[{index}/{total_count}] app_id={app_id} 在线人数={total}")

        # 最后一个请求之后无需再等待
        if index < total_count:
            time.sleep(REQUEST_INTERVAL)

    inserted = save_to_db(records)
    print(f"已写入 {inserted} 条记录到表 '{TARGET_TABLE}'（共抓取 {len(records)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
