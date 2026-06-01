"""
Shared test utilities and event factories.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.database import Database, EventRow, SessionRow
from app.ingestion import ingest_events
from app.models import EventType, IngestRequest, StoreEvent


def utc_iso(dt: datetime) -> str:
    """Format a datetime as ISO-8601 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def make_event(
    *,
    event_id: Optional[str] = None,
    store_id: str = "STORE_TEST",
    camera_id: str = "CAM_1",
    visitor_id: str = "VIS_001",
    event_type: EventType = EventType.ENTRY,
    timestamp: Optional[datetime] = None,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    queue_depth: Optional[int] = None,
    session_seq: int = 0,
) -> Dict[str, Any]:
    """Build a raw event dict suitable for API ingestion."""
    ts = timestamp or datetime(2026, 5, 31, 10, 0, 0, tzinfo=timezone.utc)
    metadata: Dict[str, Any] = {"session_seq": session_seq}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth

    return {
        "event_id": event_id or str(uuid.uuid4()),
        "camera_id": camera_id,
        "store_id": store_id,
        "visitor_id": visitor_id,
        "event_type": event_type.value,
        "timestamp": utc_iso(ts),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": metadata,
    }


def make_store_event(**kwargs: Any) -> StoreEvent:
    """Create a validated StoreEvent instance."""
    return StoreEvent.model_validate(make_event(**kwargs))


def ingest_raw_events(db: Database, events: List[Dict[str, Any]]) -> Any:
    """Ingest raw dict events through the service layer."""
    return ingest_events(db, IngestRequest(events=events))


def seed_session(
    db: Database,
    *,
    store_id: str,
    visitor_id: str,
    started_at: datetime,
    has_zone_visit: bool = False,
    has_billing_queue: bool = False,
    has_purchase: bool = False,
    is_staff: bool = False,
) -> SessionRow:
    """Insert a visitor session row for metrics and anomaly tests."""
    session = SessionRow(
        session_id=str(uuid.uuid4()),
        store_id=store_id,
        visitor_id=visitor_id,
        session_seq=0,
        started_at=utc_iso(started_at),
        ended_at=None,
        is_staff=is_staff,
        has_zone_visit=has_zone_visit,
        has_billing_queue=has_billing_queue,
        has_purchase=has_purchase,
        is_abandoned=False,
    )
    with db.connection() as conn:
        db.insert_session(conn, session)
    return session


def seed_event_row(
    db: Database,
    *,
    store_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    queue_depth: Optional[int] = None,
    event_id: Optional[str] = None,
) -> EventRow:
    """Insert a raw event row without running session logic."""
    metadata: Dict[str, Any] = {"session_seq": 0}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth

    import json

    row = EventRow(
        event_id=event_id or str(uuid.uuid4()),
        store_id=store_id,
        camera_id="CAM_1",
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=utc_iso(timestamp),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=0.9,
        metadata_json=json.dumps(metadata),
    )
    with db.connection() as conn:
        db.insert_event(conn, row)
    return row


def visitor_journey(
    store_id: str = "STORE_TEST",
    visitor_id: str = "VIS_001",
    *,
    with_purchase: bool = False,
    base_time: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Build a minimal visitor event sequence."""
    base = base_time or datetime(2026, 5, 31, 10, 0, 0, tzinfo=timezone.utc)
    events = [
        make_event(
            store_id=store_id,
            visitor_id=visitor_id,
            event_type=EventType.ENTRY,
            timestamp=base,
            zone_id="ZONE_ENTRANCE",
        ),
        make_event(
            store_id=store_id,
            visitor_id=visitor_id,
            event_type=EventType.ZONE_ENTER,
            timestamp=base + timedelta(minutes=1),
            zone_id="SKIN_CARE",
        ),
        make_event(
            store_id=store_id,
            visitor_id=visitor_id,
            event_type=EventType.BILLING_QUEUE_JOIN,
            timestamp=base + timedelta(minutes=5),
            zone_id="BILLING_QUEUE",
            queue_depth=2,
        ),
    ]
    if with_purchase:
        events.append(
            make_event(
                store_id=store_id,
                visitor_id=visitor_id,
                event_type=EventType.PURCHASE,
                timestamp=base + timedelta(minutes=8),
                zone_id="CASH_COUNTER",
            )
        )
    return events
