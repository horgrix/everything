"""
爬虫引擎：HTTP/xHR/SDK 请求 → 解析 → 清洗 → 批量写入 完整流水线。

路由规则（按优先级）：
  1. type=sdk        → SDK 流水线
  2. outputs 配置    → 多表输出（内部处理 iterate）
  3. iterate 配置    → 多值迭代
  4. 其余           → 单记录/数组展开模式
"""

import time
import logging
from .dedup import URLDedup
from .fetcher import Fetcher
from .parser import Parser
from .cleaner import Cleaner
from .anti_spider import AntiSpider
from .sdk_provider import SDKProvider
from .template import URLTemplate
from .fetcher_browser import BrowserFetcher

logger = logging.getLogger(__name__)


class CrawlerEngine:
    """爬虫引擎，协调请求/解析/清洗/存储全流程"""

    def __init__(self):
        self._url_dedup = URLDedup(cache_ttl_seconds=300)

    async def run(self, task_config: dict, db, url_context: dict = None) -> dict:
        """执行一次爬取任务，返回 {"new": ..., "updated": ..., "skipped": ..., "error": ...}"""
        if url_context is None:
            url_context = {}

        task_type = task_config.get("type", "web")

        # 分支路由
        if task_type == "sdk":
            return await self._run_sdk(task_config, db, url_context)

        if task_config.get("outputs"):
            return await self._run_outputs(task_config, db)

        if task_config.get("iterate"):
            return await self._run_iterate(task_config, db, url_context)

        return await self._run_single(task_config, db, url_context)

    # ================================================================
    # 分支处理器
    # ================================================================

    async def _run_sdk(self, task_config: dict, db, url_context: dict) -> dict:
        """SDK 流水线"""
        name = task_config.get("name", "unknown")
        stats = {"new": 0, "updated": 0, "skipped": 0, "error": None}

        try:
            provider = SDKProvider()
            raw_rows = provider.call(task_config.get("provider", {}))
            if not raw_rows:
                logger.info("SDK 任务 '%s' 返回空数据", name)
                return stats

            logger.info("SDK 返回 %d 条原始数据", len(raw_rows))
            target_table = task_config["target_table"]
            result = self._parse_and_store(
                raw_rows, task_config.get("parser", {}), target_table, db,
                context=url_context,
            )
            stats["new"], stats["updated"] = result["inserted"], result["updated"]
            stats["skipped"] = len(raw_rows) - result["inserted"] - result["updated"]
        except Exception as e:
            logger.error("SDK 任务 '%s' 失败: %s", name, e)
            stats["error"] = str(e)
        return stats

    async def _run_single(self, task_config: dict, db, url_context: dict) -> dict:
        """单请求模式（web/api 无 iterate/outputs）"""
        stats = {"new": 0, "updated": 0, "skipped": 0, "error": None}
        parser_config = task_config.get("parser", {})
        parser_fields = parser_config.get("fields", [])
        target_table = task_config["target_table"]
        task_id = task_config.get("_task_id", 0)

        raw_url = url_context.get("url") or task_config.get("url")
        url = URLTemplate.resolve(raw_url, context={"task_name": task_config.get("name", "")})
        method = task_config.get("method", "GET")

        logger.info("请求: %s %s", method, url)
        if self._url_dedup.is_duplicate(url):
            logger.info("URL 去重跳过: %s", url)
            stats["skipped"] += 1
            return stats

        try:
            raw_content = await self._fetch(task_config, url, method)
        except Exception as e:
            logger.error("请求失败: %s", e)
            stats["error"] = str(e)
            return stats

        is_array_mode = parser_config.get("root_path") is not None

        if is_array_mode:
            result = self._parse_and_store(
                raw_content, parser_config, target_table, db,
                context={"url": url},
            )
            stats["new"], stats["updated"] = result["inserted"], result["updated"]
            stats["skipped"] = result["total"] - result["inserted"] - result["updated"]
        else:
            parser = Parser()
            cleaner = Cleaner()
            parsed = parser.parse(raw_content, parser_config, context={"url": url})
            cleaned = cleaner.clean(parsed, parser_fields)
            if "source_url" in _field_names(parser_fields) and "source_url" not in cleaned:
                cleaned["source_url"] = url

            content_hash = db.hash_content(cleaned)
            dedup = db.check_dedup(url, task_id, target_table)

            if dedup is None:
                record_id = db.insert_business_record(target_table, cleaned)
                db.upsert_dedup(url, task_id, target_table, record_id, content_hash)
                stats["new"] = 1
            elif dedup["content_hash"] != content_hash:
                db.update_business_record(target_table, dedup["record_id"], cleaned)
                db.upsert_dedup(url, task_id, target_table, dedup["record_id"], content_hash)
                stats["updated"] = 1
            else:
                db.upsert_dedup(url, task_id, target_table, dedup["record_id"], content_hash)
                stats["skipped"] = 1

        return stats

    async def _run_iterate(self, task_config: dict, db, url_context: dict) -> dict:
        """多值迭代模式"""
        ic = task_config["iterate"]
        var_name, values = ic["var_name"], ic["values"]
        parser_config = task_config.get("parser", {})
        parser_fields = parser_config.get("fields", [])
        target_table = task_config["target_table"]
        raw_url = url_context.get("url") or task_config.get("url")
        method = task_config.get("method", "GET")

        logger.info("多值迭代: %s in %s (共 %d)", var_name, values, len(values))
        all_cleaned = []

        for idx, val in enumerate(values):
            ctx = {**url_context, var_name: str(val)}
            url = URLTemplate.resolve(raw_url, context=ctx)
            if self._url_dedup.is_duplicate(url):
                continue

            try:
                raw_content = await self._fetch(task_config, url, method)
            except Exception as e:
                logger.error("[%d/%d] %s=%s 失败: %s", idx + 1, len(values), var_name, val, e)
                continue

            parsed = Parser().parse_rows(raw_content, parser_config, context={"url": url, **ctx})
            for row in parsed:
                if var_name not in row:
                    row[var_name] = str(val)

            cleaned=[]
            for row in parsed:
                clean = Cleaner().clean(row, parser_fields)
                if clean is not None:
                    cleaned.append(clean)

            for row in cleaned:
                if "source_url" in _field_names(parser_fields) and "source_url" not in row:
                    row["source_url"] = url
            all_cleaned.extend(cleaned)
            logger.info("[%d/%d] %s=%s: %d 条", idx + 1, len(values), var_name, val, len(cleaned))

        if all_cleaned:
            r = db.insert_business_records_batch(target_table, all_cleaned)
            return {"new": r["inserted"], "updated": r["updated"],
                    "skipped": len(all_cleaned) - r["inserted"] - r["updated"], "error": None}
        return {"new": 0, "updated": 0, "skipped": 0, "error": None}

    async def _run_outputs(self, task_config: dict, db) -> dict:
        """多表输出模式（内部处理 iterate）"""
        outputs = task_config["outputs"]
        iterate_config = task_config.get("iterate", {})
        method = task_config.get("method", "GET")
        raw_url = task_config.get("url", "")

        has_iterate = bool(iterate_config)
        values = iterate_config.get("values", [None])
        var_name = iterate_config.get("var_name", "")

        stats = {"new": 0, "updated": 0, "skipped": 0, "error": None}

        for idx, val in enumerate(values):
            ctx = {var_name: str(val)} if has_iterate else {}
            url = URLTemplate.resolve(raw_url, context=ctx)
            if has_iterate and self._url_dedup.is_duplicate(url):
                continue

            try:
                raw_content = await self._fetch(task_config, url, method)
            except Exception as e:
                logger.error("[%d/%d] 请求失败: %s", idx + 1, len(values), e)
                continue

            for output_config in outputs:
                table = output_config["target_table"]
                pc = output_config.get("parser", {})
                pf = pc.get("fields", [])

                ts = output_config.get("table_schema", {})
                if ts:
                    db.ensure_business_table(table, ts.get("columns", []), ts.get("indexes", []))

                parsed = Parser().parse_rows(raw_content, pc, context={"url": url, **ctx})
                if not parsed:
                    continue
                for row in parsed:
                    if has_iterate and var_name not in row:
                        row[var_name] = str(val)

                cleaned = [Cleaner().clean(row, pf) for row in parsed]
                r = db.insert_business_records_batch(table, cleaned)
                stats["new"] += r["inserted"]
                stats["updated"] += r["updated"]
                logger.info("[%d/%d] 写入 '%s': +%d ~%d", idx + 1, len(values), table,
                             r["inserted"], r["updated"])

        return stats

    # ================================================================
    # 公共方法
    # ================================================================

    async def _fetch(self, task_config: dict, url: str, method: str) -> str:
        """统一请求入口：browser 模式 vs aiohttp 模式"""
        browser_config = task_config.get("browser", {})
        if browser_config:
            bf = BrowserFetcher(headless=browser_config.get("headless", True))
            try:
                return await bf.fetch(url, browser_config)
            finally:
                await bf.close()

        anti = AntiSpider(task_config.get("anti_spider", {}))
        retry = task_config.get("retry", {})
        encoding = task_config.get("encoding", 'utf-8')
        fetcher = Fetcher(
            max_retries=retry.get("max_attempts", 3),
            backoff_base=retry.get("backoff_base", 2.0),
        )
        async with anti:
            return await fetcher.fetch(url, method=method, encoding=encoding)

    def _parse_and_store(self, raw_content, parser_config: dict, target_table: str,
                          db, context: dict = None) -> dict:
        """解析 → 清洗 → 批量写入，返回 {"inserted": N, "updated": N, "total": N}"""
        parser = Parser()
        cleaner = Cleaner()
        parser_fields = parser_config.get("fields", [])

        parsed = parser.parse_rows(raw_content, parser_config, context=context)
        cleaned = [cleaner.clean(row, parser_fields) for row in parsed]

        src_fields = _field_names(parser_fields)
        for row in cleaned:
            if "source_url" in src_fields and "source_url" not in row:
                row["source_url"] = context.get("url", "") if context else ""

        result = db.insert_business_records_batch(target_table, cleaned)
        return {"inserted": result["inserted"], "updated": result["updated"], "total": len(cleaned)}


def _field_names(fields: list[dict]) -> list[str]:
    return [f["name"] for f in fields]