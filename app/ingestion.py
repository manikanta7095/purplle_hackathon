"""
Event ingestion endpoint and visitor session lifecycle management.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.database import Database, EventRow, SessionRow
from app.models import (
    PURCHASE_ZONES,
    SYSTEM_ZONES,
    EventType,
    IngestError,
    IngestRequest,
    IngestResponse,
    StoreEvent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def get_database() -> Database:
    """FastAPI dependency placeholder overridden in main."""
    raise RuntimeError("Database dependency not configured")


def _is_retail_zone(zone_id: Optional[str]) -> bool:
    return bool(zone_id) and zone_id not in SYSTEM_ZONES


def _is_purchase_event(event_type: str, zone_id: Optional[str]) -> bool:
    if event_type == EventType.PURCHASE.value:
        return True
    return event_type == EventType.ZONE_ENTER.value and zone_id in PURCHASE_ZONES


def event_to_row(event: StoreEvent) -> EventRow:
    """Convert a validated Pydantic event to a database row."""
    return EventRow(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type.value,
        timestamp=event.timestamp.isoformat(),
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        metadata_json=json.dumps(event.metadata.model_dump()),
    )


def apply_session_update(session: SessionRow, event: StoreEvent) -> None:
    """
    Mutate session flags based on a single event.

    ENTRY starts sessions; EXIT closes them. REENTRY resumes an open session
    without creating a duplicate funnel entry.
    """
    event_type = event.event_type.value

    if event_type in {EventType.ZONE_ENTER.value, EventType.ZONE_EXIT.value}:
        if _is_retail_zone(event.zone_id):
            session.has_zone_visit = True

    if event_type == EventType.BILLING_QUEUE_JOIN.value:
        session.has_billing_queue = True

    if _is_purchase_event(event_type, event.zone_id):
        session.has_purchase = True

    if event_type == EventType.BILLING_QUEUE_ABANDON.value and not session.has_purchase:
        session.is_abandoned = True

    if event_type == EventType.EXIT.value:
        session.ended_at = event.timestamp.isoformat()


def process_session_for_event(db: Database, event: StoreEvent) -> None:
    """
    Create or update visitor sessions for a newly ingested event.

    Staff sessions are ignored. Re-entry continues the same visitor identity
    by resuming an open session or starting a new one only on ENTRY when none
    is active.
    """
    if event.is_staff:
        return

    event_type = event.event_type.value

    with db.connection() as conn:
        open_session = db.get_open_session(conn, event.store_id, event.visitor_id)

        if event_type == EventType.ENTRY.value:
            if open_session is not None:
                apply_session_update(open_session, event)
                db.update_session(conn, open_session)
                return

            session = SessionRow(
                session_id=str(uuid.uuid4()),
                store_id=event.store_id,
                visitor_id=event.visitor_id,
                session_seq=event.metadata.session_seq,
                started_at=event.timestamp.isoformat(),
                ended_at=None,
                is_staff=False,
                has_zone_visit=False,
                has_billing_queue=False,
                has_purchase=False,
                is_abandoned=False,
            )
            apply_session_update(session, event)
            db.insert_session(conn, session)
            return

        if event_type == EventType.REENTRY.value:
            if open_session is None:
                session = SessionRow(
                    session_id=str(uuid.uuid4()),
                    store_id=event.store_id,
                    visitor_id=event.visitor_id,
                    session_seq=event.metadata.session_seq,
                    started_at=event.timestamp.isoformat(),
                    ended_at=None,
                    is_staff=False,
                    has_zone_visit=False,
                    has_billing_queue=False,
                    has_purchase=False,
                    is_abandoned=False,
                )
                apply_session_update(session, event)
                db.insert_session(conn, session)
            else:
                apply_session_update(open_session, event)
                db.update_session(conn, open_session)
            return

        if open_session is None:
            return

        apply_session_update(open_session, event)
        db.update_session(conn, open_session)


def ingest_events(db: Database, payload: IngestRequest) -> IngestResponse:
    """
    Validate, deduplicate, and persist a batch of events.

    Supports partial success: valid events are stored even when others fail.
    Re-posting the same event_id is idempotent and counted as duplicate.
    """
    accepted = 0
    rejected = 0
    duplicates = 0
    errors: List[IngestError] = []
    to_process: List[Tuple[int, StoreEvent]] = []

    seen_in_batch: set[str] = set()

    for index, raw in enumerate(payload.events):
        try:
            event = raw if isinstance(raw, StoreEvent) else StoreEvent.model_validate(raw)
        except ValidationError as exc:
            rejected += 1
            errors.append(
                IngestError(
                    event_id=getattr(raw, "event_id", None),
                    index=index,
                    message=str(exc.errors()[0]["msg"]),
                )
            )
            continue

        if event.event_id in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(event.event_id)

        if db.event_exists(event.event_id):
            duplicates += 1
            continue

        to_process.append((index, event))

    if to_process:
        sorted_events = sorted(to_process, key=lambda item: item[1].timestamp)
        successful: List[StoreEvent] = []
        with db.connection() as conn:
            for index, event in sorted_events:
                try:
                    inserted = db.insert_event(conn, event_to_row(event))
                    if inserted:
                        successful.append(event)
                        accepted += 1
                    else:
                        duplicates += 1
                except Exception as exc:
                    rejected += 1
                    errors.append(
                        IngestError(
                            event_id=event.event_id,
                            index=index,
                            message=str(exc),
                        )
                    )

        for event in successful:
            try:
                process_session_for_event(db, event)
            except Exception:
                logger.exception(
                    "session update failed event_id=%s visitor=%s",
                    event.event_id,
                    event.visitor_id,
                )

    logger.info(
        "ingestion complete accepted=%s rejected=%s duplicates=%s",
        accepted,
        rejected,
        duplicates,
    )
    return IngestResponse(
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        errors=errors,
    )


@router.post("/ingest", response_model=IngestResponse)
def ingest_endpoint(
    request: Request,
    payload: IngestRequest,
    db: Database = Depends(get_database),
) -> IngestResponse:
    """Accept up to 500 events with idempotent, partial-success ingestion."""
    request.state.event_count = len(payload.events)
    if payload.events:
        first = payload.events[0]
        store_id = first.get("store_id") if isinstance(first, dict) else getattr(first, "store_id", None)
        if store_id:
            request.state.store_id = store_id
    return ingest_events(db, payload)
