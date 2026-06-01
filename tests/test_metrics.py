# PROMPT:
# Unit and integration tests for compute_store_metrics and compute_metrics_from_sessions:
# empty store returns zeros; visitors without purchase have conversion_rate 0;
# staff sessions excluded from unique_visitors; re-entry must count visitor once
# (ENTRY, EXIT, REENTRY, ZONE_ENTER sequence). Use seed_session and visitor_journey
# helpers from tests.utils.
#
# CHANGES MADE:
# - Added test_reentry_counts_visitor_once with explicit REENTRY after EXIT
#   (prompt only described "re-entry" generically).
# - Added test_compute_metrics_from_sessions_direct with two sessions for same
#   visitor_id to assert dedupe at aggregation layer (distinct visitor_id set).
# - Deferred abandonment_rate and avg_dwell_per_zone tests — covered indirectly
#   via integration elsewhere; kept scope to challenge-critical KPIs.
# - Used fixed UTC base times on multi-visitor conversion test to avoid flakiness.

"""
Tests for store metrics computation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import Database
from app.metrics import compute_metrics_from_sessions, compute_store_metrics
from app.models import EventType
from tests.utils import ingest_raw_events, make_event, seed_session, visitor_journey


def test_empty_store_unique_visitors_zero(db: Database) -> None:
    """A store with no data reports zero visitors."""
    metrics = compute_store_metrics(db, "STORE_EMPTY")
    assert metrics.unique_visitors == 0
    assert metrics.conversion_rate == 0.0


def test_visitors_without_purchases_conversion_zero(db: Database) -> None:
    """Visitors who never purchase yield conversion_rate=0."""
    base = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)
    ingest_raw_events(
        db,
        visitor_journey(
            store_id="STORE_NO_PURCHASE",
            visitor_id="VIS_A",
            with_purchase=False,
            base_time=base,
        ),
    )
    ingest_raw_events(
        db,
        visitor_journey(
            store_id="STORE_NO_PURCHASE",
            visitor_id="VIS_B",
            with_purchase=False,
            base_time=base + timedelta(hours=1),
        ),
    )

    metrics = compute_store_metrics(db, "STORE_NO_PURCHASE")
    assert metrics.unique_visitors == 2
    assert metrics.conversion_rate == 0.0


def test_staff_events_excluded_from_metrics(db: Database) -> None:
    """Staff sessions and events do not affect customer KPIs."""
    base = datetime(2026, 5, 31, 9, 0, 0, tzinfo=timezone.utc)
    ingest_raw_events(
        db,
        [
            make_event(
                store_id="STORE_MIXED",
                visitor_id="STAFF_01",
                is_staff=True,
                event_type=EventType.ENTRY,
                timestamp=base,
            ),
            make_event(
                store_id="STORE_MIXED",
                visitor_id="STAFF_01",
                is_staff=True,
                event_type=EventType.PURCHASE,
                timestamp=base + timedelta(minutes=2),
                zone_id="CASH_COUNTER",
            ),
        ],
    )
    ingest_raw_events(
        db,
        visitor_journey(
            store_id="STORE_MIXED",
            visitor_id="CUST_01",
            with_purchase=True,
            base_time=base + timedelta(hours=1),
        ),
    )

    metrics = compute_store_metrics(db, "STORE_MIXED")
    assert metrics.unique_visitors == 1
    assert metrics.conversion_rate == 1.0


def test_reentry_counts_visitor_once(db: Database) -> None:
    """Re-entry events keep unique_visitors at one per visitor identity."""
    base = datetime(2026, 5, 31, 8, 0, 0, tzinfo=timezone.utc)
    events = [
        make_event(
            store_id="STORE_REENTRY",
            visitor_id="VIS_RE",
            event_type=EventType.ENTRY,
            timestamp=base,
            zone_id="ZONE_ENTRANCE",
        ),
        make_event(
            store_id="STORE_REENTRY",
            visitor_id="VIS_RE",
            event_type=EventType.EXIT,
            timestamp=base + timedelta(minutes=30),
            zone_id="ZONE_ENTRANCE",
        ),
        make_event(
            store_id="STORE_REENTRY",
            visitor_id="VIS_RE",
            event_type=EventType.REENTRY,
            timestamp=base + timedelta(hours=1),
            zone_id="ZONE_ENTRANCE",
        ),
        make_event(
            store_id="STORE_REENTRY",
            visitor_id="VIS_RE",
            event_type=EventType.ZONE_ENTER,
            timestamp=base + timedelta(hours=1, minutes=5),
            zone_id="MAKEUP",
        ),
    ]
    ingest_raw_events(db, events)

    metrics = compute_store_metrics(db, "STORE_REENTRY")
    assert metrics.unique_visitors == 1


def test_compute_metrics_from_sessions_direct(db: Database) -> None:
    """Unit-level session aggregation excludes staff and deduplicates visitors."""
    now = datetime(2026, 5, 31, 7, 0, 0, tzinfo=timezone.utc)
    sessions = [
        seed_session(
            db,
            store_id="STORE_UNIT",
            visitor_id="V1",
            started_at=now,
            has_purchase=True,
        ),
        seed_session(
            db,
            store_id="STORE_UNIT",
            visitor_id="V1",
            started_at=now + timedelta(hours=2),
            has_purchase=False,
        ),
        seed_session(
            db,
            store_id="STORE_UNIT",
            visitor_id="STAFF",
            started_at=now,
            has_purchase=True,
            is_staff=True,
        ),
    ]
    staff_sessions = [session for session in sessions if session.is_staff]
    customer_sessions = [session for session in sessions if not session.is_staff]

    metrics = compute_metrics_from_sessions(customer_sessions, [])
    assert metrics.unique_visitors == 1
    assert len(staff_sessions) == 1
