"""
Service health checks and feed freshness monitoring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends

from app.database import Database
from app.ingestion import get_database
from app.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

STALE_FEED_MINUTES = 10


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_health_response(db: Database) -> HealthResponse:
    """
    Build a health snapshot including feed freshness warnings.

    Adds STALE_FEED when no event has been received for more than 10 minutes.
    """
    warnings: List[str] = []
    last_ts = db.last_event_timestamp()
    stores = db.list_store_ids()

    if last_ts is None:
        warnings.append("NO_EVENTS: No events have been ingested yet")
    else:
        last_dt = _parse_ts(last_ts)
        now = datetime.now(timezone.utc)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age = now - last_dt
        if age > timedelta(minutes=STALE_FEED_MINUTES):
            warnings.append("STALE_FEED")

    status = "healthy"
    return HealthResponse(
        status=status,
        last_event_timestamp=last_ts,
        stores=stores,
        warnings=warnings,
    )


@router.get("/health", response_model=HealthResponse)
def health_check(db: Database = Depends(get_database)) -> HealthResponse:
    """Return service health and ingestion feed status."""
    return build_health_response(db)
