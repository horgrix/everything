"""Shared test fixtures."""

import pytest
from storage.database import Database


@pytest.fixture
def db():
    """In-memory SQLite database with system tables initialized."""
    database = Database(":memory:")
    database.init_system_tables()
    yield database
    database.close()
