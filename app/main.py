"""
FastAPI application entry point for the store intelligence API.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app import __version__
from app.anomalies import router as anomalies_router
from app.database import Database, DEFAULT_DB_PATH
from app.exceptions import register_exception_handlers
from app.funnel import router as funnel_router
from app.health import router as health_router
from app.ingestion import get_database, router as ingestion_router
from app.logging_config import configure_json_logging
from app.metrics import router as metrics_router
from app.middleware import StructuredLoggingMiddleware

logger = logging.getLogger(__name__)

_db: Database | None = None


def get_app_database() -> Database:
    """Return the singleton database instance."""
    global _db
    if _db is None:
        _db = Database(DEFAULT_DB_PATH)
    return _db


def reset_app_database(db: Database | None = None) -> None:
    """Reset the singleton database (used by tests)."""
    global _db
    _db = db


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize resources on startup and release on shutdown."""
    configure_json_logging()
    db = get_app_database()
    logger.info("store intelligence API started db=%s version=%s", db.db_path, __version__)
    yield
    logger.info("store intelligence API shutting down")


def create_app() -> FastAPI:
    """Application factory for testing and production."""
    application = FastAPI(
        title="Purplle Store Intelligence API",
        description="Production-grade retail analytics service for CCTV event ingestion and metrics.",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    application.add_middleware(StructuredLoggingMiddleware)
    application.dependency_overrides[get_database] = get_app_database
    register_exception_handlers(application)

    application.include_router(health_router)
    application.include_router(ingestion_router)
    application.include_router(metrics_router)
    application.include_router(funnel_router)
    application.include_router(anomalies_router)

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    configure_json_logging()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
