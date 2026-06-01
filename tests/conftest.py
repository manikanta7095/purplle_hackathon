"""
Pytest fixtures for the store intelligence test suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.ingestion import get_database
from app.main import create_app, reset_app_database


@pytest.fixture
def db(tmp_path: Path) -> Generator[Database, None, None]:
    """Provide an isolated SQLite database per test."""
    database = Database(tmp_path / "test.db")
    yield database
    reset_app_database(None)


@pytest.fixture
def client(db: Database) -> Generator[TestClient, None, None]:
    """FastAPI test client with database dependency override."""
    application = create_app()
    application.dependency_overrides[get_database] = lambda: db
    reset_app_database(db)

    with TestClient(application) as test_client:
        yield test_client

    application.dependency_overrides.clear()
    reset_app_database(None)
