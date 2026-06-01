# PROMPT:
# Write pytest tests for POST /events/ingest covering: valid batch acceptance,
# idempotent duplicate event_id (same payload twice must not change metrics),
# partial rejection when one event has an invalid UUID, empty batch 422,
# staff-only ingest without creating visitor_sessions, duplicate IDs within
# one batch, and 503 DATABASE_UNAVAILABLE without leaking tracebacks.
# Use FastAPI TestClient and an isolated SQLite DB via existing conftest fixtures.
#
# CHANGES MADE:
# - Added test_ingest_events_layer_duplicate calling ingest_events() directly
#   (not only HTTP) after review found gap in service-layer coverage.
# - Tightened duplicate test to assert metrics_before == metrics_after, not
#   just row counts.
# - Renamed store IDs (STORE_DUP, STORE_INGEST) to avoid cross-test pollution
#   when tests run in random order.
# - Staff session test uses STORE_TEST and asserts unique_visitors == 0 on API.

"""
Tests for event ingestion pipeline behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.ingestion import ingest_events
from app.models import EventType, IngestRequest
from tests.utils import ingest_raw_events, make_event, visitor_journey


def test_valid_event_ingestion(client: TestClient, db: Database) -> None:
    """Valid events are accepted and persisted."""
    events = visitor_journey(store_id="STORE_INGEST")
    response = client.post("/events/ingest", json={"events": events})

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == len(events)
    assert body["rejected"] == 0
    assert body["duplicates"] == 0
    assert "X-Trace-Id" in response.headers

    stored = db.fetch_events_for_store("STORE_INGEST", exclude_staff=False)
    assert len(stored) == len(events)


def test_duplicate_ingestion_is_idempotent(client: TestClient, db: Database) -> None:
    """Re-posting the same payload yields accepted=0 and duplicates=n."""
    events = visitor_journey(store_id="STORE_DUP")
    first = client.post("/events/ingest", json={"events": events})
    assert first.status_code == 200
    assert first.json()["accepted"] == len(events)

    metrics_before = client.get("/stores/STORE_DUP/metrics").json()["data"]

    second = client.post("/events/ingest", json={"events": events})
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["accepted"] == 0
    assert second_body["duplicates"] == len(events)
    assert second_body["rejected"] == 0

    stored = db.fetch_events_for_store("STORE_DUP", exclude_staff=False)
    assert len(stored) == len(events)

    metrics_after = client.get("/stores/STORE_DUP/metrics").json()["data"]
    assert metrics_after == metrics_before


def test_malformed_events_partial_rejection(client: TestClient) -> None:
    """Invalid events are rejected while valid ones are accepted."""
    valid = make_event(visitor_id="VIS_OK")
    invalid = make_event(visitor_id="VIS_BAD")
    invalid["event_id"] = "not-a-uuid"

    response = client.post("/events/ingest", json={"events": [valid, invalid]})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    assert len(body["errors"]) == 1


def test_empty_payload_returns_validation_error(client: TestClient) -> None:
    """Empty event batches are rejected at validation."""
    response = client.post("/events/ingest", json={"events": []})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "VALIDATION_ERROR"
    assert "trace_id" in body


def test_all_staff_events_accepted_without_sessions(client: TestClient, db: Database) -> None:
    """Staff events ingest successfully but do not create visitor sessions."""
    events = [
        make_event(visitor_id="STAFF_1", is_staff=True, event_type=EventType.ENTRY),
        make_event(
            visitor_id="STAFF_1",
            is_staff=True,
            event_type=EventType.ZONE_ENTER,
            zone_id="SKIN_CARE",
        ),
    ]
    response = client.post("/events/ingest", json={"events": events})
    assert response.status_code == 200
    assert response.json()["accepted"] == 2

    sessions = db.fetch_sessions_for_store("STORE_TEST", exclude_staff=False)
    assert sessions == []

    metrics = client.get("/stores/STORE_TEST/metrics").json()["data"]
    assert metrics["unique_visitors"] == 0


def test_duplicate_event_id_in_batch(client: TestClient) -> None:
    """Duplicate IDs within a single batch count as duplicates."""
    event = make_event()
    payload = {"events": [event, dict(event)]}
    response = client.post("/events/ingest", json=payload)
    body = response.json()
    assert body["accepted"] == 1
    assert body["duplicates"] == 1


def test_ingest_events_layer_duplicate(db: Database) -> None:
    """Service-layer ingestion is idempotent on event_id."""
    raw = make_event(store_id="STORE_SVC")
    first = ingest_events(db, IngestRequest(events=[raw]))
    second = ingest_events(db, IngestRequest(events=[raw]))

    assert first.accepted == 1
    assert second.accepted == 0
    assert second.duplicates == 1


def test_database_unavailable_returns_503(client: TestClient, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Database failures surface as DATABASE_UNAVAILABLE without stack traces."""
    from contextlib import contextmanager

    from app.exceptions import DatabaseUnavailable

    @contextmanager
    def _fail_connection() -> None:
        raise DatabaseUnavailable("simulated outage")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "connection", _fail_connection)

    response = client.post(
        "/events/ingest",
        json={"events": [make_event()]},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "DATABASE_UNAVAILABLE"
    assert body["message"] == "Database temporarily unavailable"
    assert "trace_id" in body
    assert "Traceback" not in response.text
