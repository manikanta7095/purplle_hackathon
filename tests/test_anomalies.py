# PROMPT:
# Test anomaly detectors in app/anomalies.py: QUEUE_SPIKE when latest queue_depth
# exceeds 1.5x rolling average; CONVERSION_DROP when rate falls below 85% of
# 7-day baseline; DEAD_ZONE when no ZONE_ENTER in a retail zone for 30+ minutes.
# Mock datetime.utcnow where needed for deterministic windows. Include at least one
# integration test via detect_anomalies(db, store_id) returning ApiResponse shape.
#
# CHANGES MADE:
# - Built queue spike fixtures with metadata_json=json.dumps(...) on EventRow
#   (initial draft passed metadata= dict, which is not a dataclass field).
# - conversion_drop test patches app.metrics.datetime.now for a fixed "today"
#   instead of mocking wall clock in anomalies module directly.
# - test_no_anomaly_state asserts detect_anomalies returns [] end-to-end, not
#   only isolated detector functions.
# - dead_zone uses retail zone FRAGRANCE vs active SKIN_CARE contrast.

"""
Tests for operational anomaly detection.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.anomalies import (
    detect_anomalies,
    detect_conversion_drop,
    detect_dead_zones,
    detect_queue_spike,
)
from app.database import Database
from app.models import AnomalyType, EventType
from app.database import EventRow
from tests.utils import seed_event_row, seed_session, utc_iso


def test_queue_spike_anomaly() -> None:
    """Elevated queue depth versus rolling average triggers QUEUE_SPIKE."""
    now = datetime(2026, 5, 31, 14, 0, 0, tzinfo=timezone.utc)

    rows = [
        EventRow(
            event_id=f"e{i}",
            store_id="S",
            camera_id="CAM",
            visitor_id="V",
            event_type=EventType.BILLING_QUEUE_JOIN.value,
            timestamp=utc_iso(now - timedelta(minutes=5 - i)),
            zone_id="BILLING_QUEUE",
            dwell_ms=0,
            is_staff=False,
            confidence=0.9,
            metadata_json=json.dumps({"queue_depth": depth}),
        )
        for i, depth in enumerate([2, 2, 3, 2, 2, 10])
    ]

    anomaly = detect_queue_spike(rows)
    assert anomaly is not None
    assert anomaly.type == AnomalyType.QUEUE_SPIKE


def test_dead_zone_anomaly(db: Database) -> None:
    """Zones without recent visits are flagged as DEAD_ZONE."""
    now = datetime(2026, 5, 31, 15, 0, 0, tzinfo=timezone.utc)
    seed_event_row(
        db,
        store_id="STORE_DEAD",
        visitor_id="VIS_1",
        event_type=EventType.ZONE_ENTER.value,
        timestamp=now - timedelta(minutes=45),
        zone_id="FRAGRANCE",
    )
    seed_event_row(
        db,
        store_id="STORE_DEAD",
        visitor_id="VIS_2",
        event_type=EventType.ZONE_ENTER.value,
        timestamp=now - timedelta(minutes=5),
        zone_id="SKIN_CARE",
    )

    events = db.fetch_events_for_store("STORE_DEAD", exclude_staff=True)
    findings = detect_dead_zones(events, now=now)
    dead_zones = {finding.zone_id for finding in findings}
    assert "FRAGRANCE" in dead_zones
    assert "SKIN_CARE" not in dead_zones


def test_conversion_drop_anomaly(db: Database) -> None:
    """Conversion materially below the 7-day baseline triggers CONVERSION_DROP."""
    now = datetime(2026, 5, 31, 16, 0, 0, tzinfo=timezone.utc)
    store_id = "STORE_CONV"

    for day_offset in range(1, 8):
        started = now - timedelta(days=day_offset)
        for visitor_index in range(4):
            seed_session(
                db,
                store_id=store_id,
                visitor_id=f"hist_{day_offset}_{visitor_index}",
                started_at=started,
                has_purchase=True,
            )

    for visitor_index in range(4):
        seed_session(
            db,
            store_id=store_id,
            visitor_id=f"today_{visitor_index}",
            started_at=now.replace(hour=10, minute=0, second=0, microsecond=0),
            has_purchase=visitor_index == 0,
        )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return now

    with patch("app.metrics.datetime", FixedDatetime):
        anomaly = detect_conversion_drop(db, store_id)

    assert anomaly is not None
    assert anomaly.type == AnomalyType.CONVERSION_DROP


def test_no_anomaly_state(db: Database) -> None:
    """Balanced traffic and conversion produce no anomalies."""
    now = datetime(2026, 5, 31, 11, 0, 0, tzinfo=timezone.utc)
    store_id = "STORE_OK"

    for index in range(3):
        seed_session(
            db,
            store_id=store_id,
            visitor_id=f"visitor_{index}",
            started_at=now - timedelta(minutes=index * 5),
            has_zone_visit=True,
            has_purchase=True,
        )
        seed_event_row(
            db,
            store_id=store_id,
            visitor_id=f"visitor_{index}",
            event_type=EventType.ZONE_ENTER.value,
            timestamp=now - timedelta(minutes=index + 1),
            zone_id="SKIN_CARE",
            queue_depth=2,
        )

    events = db.fetch_events_for_store(store_id, exclude_staff=True)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return now

    with patch("app.metrics.datetime", FixedDatetime):
        findings = detect_anomalies(db, store_id)

    assert findings == []
    assert detect_queue_spike(events) is None
