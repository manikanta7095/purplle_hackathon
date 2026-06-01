"""
Load bundled demo events when the database is empty (e.g. fresh Render deploy).

Set AUTO_SEED_EVENTS=true on the host to enable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from app.database import Database
from app.ingestion import IngestRequest, ingest_events
from app.models import StoreEvent

logger = logging.getLogger(__name__)

DEMO_EVENTS_PATH = Path(__file__).resolve().parent.parent / "demo" / "events_all.jsonl"
BATCH_SIZE = 500


def _auto_seed_enabled() -> bool:
    return os.getenv("AUTO_SEED_EVENTS", "").strip().lower() in ("1", "true", "yes")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _event_count(db: Database) -> int:
    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
    return int(row["n"]) if row else 0


def seed_demo_events_if_empty(db: Database) -> None:
    """Ingest demo/events_all.jsonl when AUTO_SEED_EVENTS is set and DB has no events."""
    if not _auto_seed_enabled():
        return
    if _event_count(db) > 0:
        logger.info("demo seed skipped database already has events")
        return
    if not DEMO_EVENTS_PATH.is_file():
        logger.warning("demo seed skipped file missing path=%s", DEMO_EVENTS_PATH)
        return

    raw = _load_jsonl(DEMO_EVENTS_PATH)
    if not raw:
        logger.warning("demo seed skipped empty file path=%s", DEMO_EVENTS_PATH)
        return

    events = [StoreEvent.model_validate(row) for row in raw]
    accepted = 0
    for offset in range(0, len(events), BATCH_SIZE):
        batch = events[offset : offset + BATCH_SIZE]
        result = ingest_events(db, IngestRequest(events=batch))
        accepted += result.accepted

    logger.info(
        "demo seed complete path=%s total=%s accepted=%s",
        DEMO_EVENTS_PATH,
        len(events),
        accepted,
    )
