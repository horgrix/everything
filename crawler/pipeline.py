"""
Data pipeline: parse → clean → filter → inject → write.

Extracted from CrawlerEngine so the engine only orchestrates
while the pipeline handles all data transformation.
"""

from dataclasses import dataclass, field
import logging
from typing import Any

from .parser import Parser
from .cleaner import Cleaner

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing one output target."""
    inserted: int = 0
    updated: int = 0
    total: int = 0

    def __add__(self, other: "PipelineResult") -> "PipelineResult":
        return PipelineResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            total=self.total + other.total,
        )


class DataPipeline:
    """
    Stateless data transformation pipeline.

    Takes raw data from a DataSource and an output config,
    runs the full chain: parse → clean → filter → inject → upsert.

    Usage:
        pipeline = DataPipeline(parser, cleaner, db)
        result = pipeline.process(raw_data, output_config, context)
    """

    def __init__(self, parser: Parser = None, cleaner: Cleaner = None):
        self._parser = parser or Parser()
        self._cleaner = cleaner or Cleaner()

    def process(
        self,
        raw_data: Any,
        output_config: dict,
        db,
        context: dict,
    ) -> PipelineResult:
        """
        Process one output target against raw data.

        Steps:
          1. Ensure target table exists
          2. Extract page-level elements (element_selector)
          3. Parse raw data into rows
          4. Clean & filter rows
          5. Inject source_url
          6. Batch UPSERT into database

        Returns:
            PipelineResult with inserted/updated/total counts.
        """
        table = output_config["target_table"]
        parser_config = output_config.get("parser", {})
        parser_fields = parser_config.get("fields", [])
        table_schema = output_config.get("table_schema", {})

        # 1. Ensure business table exists
        if table_schema:
            db.ensure_business_table(
                table,
                table_schema.get("columns", []),
                table_schema.get("indexes", []),
            )

        # 2. Extract page-level elements into context
        element_selector_config = parser_config.get("element_selector", {})
        if element_selector_config:
            element_vars = self._parser.extract_element_vars(
                raw_data, element_selector_config
            )
            context.update(element_vars)

        # 3. Parse
        parsed = self._parser.parse_rows(raw_data, parser_config, context=context)
        if not parsed:
            return PipelineResult()

        # 4. Clean & filter
        cleaned = self._cleaner.clean_batch(parsed, parser_fields)

        # 5. Inject source_url
        src_field_names = Cleaner.field_names(parser_fields)
        url = context.get("url", "")
        if "source_url" in src_field_names and url:
            for row in cleaned:
                if "source_url" not in row:
                    row["source_url"] = url

        # 6. Batch upsert
        result = db.insert_business_records_batch(table, cleaned)
        return PipelineResult(
            inserted=result["inserted"],
            updated=result["updated"],
            total=len(cleaned),
        )
