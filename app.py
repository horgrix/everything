"""
Application assembly: wires all dependencies together.

This factory replaces the ad-hoc object creation spread across
main.py, scheduler.py, and api/routes/tasks.py with a single
composable entry point.
"""

import os
import logging
from typing import NamedTuple

from storage.database import Database
from crawler.engine import CrawlerEngine
from crawler.pipeline import DataPipeline
from crawler.parser import Parser
from crawler.cleaner import Cleaner
from crawler.sources import create_default_registry, SourceRegistry
from scheduler.scheduler import CrawlScheduler
from task_manager.loader import TaskLoader

logger = logging.getLogger(__name__)


class App(NamedTuple):
    """All wired components."""
    engine: CrawlerEngine
    scheduler: CrawlScheduler
    loader: TaskLoader
    db: Database
    config_dir: str
    db_path: str


def create_app(
    config_dir: str = "config/tasks",
    db_path: str = "crawler.db",
    sources: SourceRegistry = None,
) -> App:
    """
    Assemble the application with all dependencies injected.

    Args:
        config_dir: Path to YAML task config directory.
        db_path: Path to SQLite database file.
        sources: Optional custom SourceRegistry. If None, uses defaults.

    Returns:
        App named tuple with all wired components.
    """
    # Ensure config directory exists
    os.makedirs(config_dir, exist_ok=True)

    # Storage
    db = Database(db_path)
    db.init_system_tables()
    logger.info("Database ready: %s", os.path.abspath(db_path))

    # Data sources
    if sources is None:
        sources = create_default_registry()
    logger.info("Data sources: %s", sources.get_all_types())

    # Processing
    pipeline = DataPipeline(parser=Parser(), cleaner=Cleaner())

    # Engine
    engine = CrawlerEngine(sources=sources, pipeline=pipeline)

    # Task loader
    loader = TaskLoader(config_dir, db)

    # Scheduler (needs engine + loader + db)
    scheduler = CrawlScheduler(
        config_dir=config_dir,
        db=db,
        engine=engine,
        loader=loader,
    )

    return App(
        engine=engine,
        scheduler=scheduler,
        loader=loader,
        db=db,
        config_dir=config_dir,
        db_path=db_path,
    )
