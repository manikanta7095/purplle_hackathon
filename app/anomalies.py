"""
Operational anomaly detection for retail stores.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database, EventRow
from app.ingestion import get_database
from app.metrics import compute_daily_conversion_rates, compute_store_metrics, safe_divide
from app.models import (
    SYSTEM_ZONES,
    Anomaly,
    AnomalySeverity,
    AnomalyType,
    ApiResponse,
    EventType,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stores", tags=["anomalies"])

QUEUE_SPIKE_MULTIPLIER = 1.5
CONVERSION_DROP_THRESHOLD = 0.85
DEAD_ZONE_MINUTES = 30
ROLLING_QUEUE_WINDOW = 20


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def detect_queue_spike(events: List[EventRow]) -> Optional[Anomaly]:
    """
    Flag when the latest queue depth exceeds the rolling average.

    Uses the most recent events with queue_depth metadata.
    """
    depths: List[int] = []
    for event in reversed(events):
        if event.is_staff:
            continue
        depth = event.metadata.get("queue_depth")
        if depth is not None:
            depths.append(int(depth))
        if len(depths) >= ROLLING_QUEUE_WINDOW:
            break

    if len(depths) < 2:
        return None

    current = depths[0]
    rolling_avg = sum(depths[1:]) / len(depths[1:])
    if rolling_avg <= 0:
        if current >= 3:
            return Anomaly(
                type=AnomalyType.QUEUE_SPIKE,
                severity=AnomalySeverity.WARN,
                suggested_action="Open additional billing counter",
                detail=f"Queue depth {current} with no historical baseline",
            )
        return None

    if current > rolling_avg * QUEUE_SPIKE_MULTIPLIER:
        severity = (
            AnomalySeverity.CRITICAL
            if current > rolling_avg * 2
            else AnomalySeverity.WARN
        )
        return Anomaly(
            type=AnomalyType.QUEUE_SPIKE,
            severity=severity,
            suggested_action="Open additional billing counter",
            detail=f"Current queue {current} vs rolling avg {rolling_avg:.1f}",
        )
    return None


def detect_conversion_drop(db: Database, store_id: str) -> Optional[Anomaly]:
    """Flag when today's conversion falls below the 7-day average."""
    daily_rates = compute_daily_conversion_rates(db, store_id, days=7)
    if len(daily_rates) < 2:
        return None

    current = daily_rates[0]
    baseline = sum(daily_rates[1:]) / len(daily_rates[1:])
    if baseline <= 0:
        return None

    if current < baseline * CONVERSION_DROP_THRESHOLD:
        severity = (
            AnomalySeverity.CRITICAL
            if current < baseline * 0.5
            else AnomalySeverity.WARN
        )
        return Anomaly(
            type=AnomalyType.CONVERSION_DROP,
            severity=severity,
            suggested_action="Review staffing and in-store promotions",
            detail=f"Current conversion {current:.2%} vs 7-day avg {baseline:.2%}",
        )
    return None


def detect_dead_zones(events: List[EventRow], now: Optional[datetime] = None) -> List[Anomaly]:
    """Flag retail zones with no visits in the last 30 minutes."""
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(minutes=DEAD_ZONE_MINUTES)

    last_visit: Dict[str, datetime] = {}
    all_zones: set[str] = set()

    for event in events:
        if event.is_staff or not event.zone_id or event.zone_id in SYSTEM_ZONES:
            continue
        if event.event_type not in {
            EventType.ZONE_ENTER.value,
            EventType.ZONE_DWELL.value,
        }:
            continue

        all_zones.add(event.zone_id)
        ts = _parse_ts(event.timestamp)
        previous = last_visit.get(event.zone_id)
        if previous is None or ts > previous:
            last_visit[event.zone_id] = ts

    anomalies: List[Anomaly] = []
    for zone_id in sorted(all_zones):
        last = last_visit.get(zone_id)
        if last is None or last < cutoff:
            minutes_idle = (
                int((reference - last).total_seconds() / 60) if last else DEAD_ZONE_MINUTES
            )
            anomalies.append(
                Anomaly(
                    type=AnomalyType.DEAD_ZONE,
                    severity=(
                        AnomalySeverity.CRITICAL
                        if minutes_idle >= 60
                        else AnomalySeverity.WARN
                    ),
                    suggested_action="Inspect zone merchandising and staff allocation",
                    zone_id=zone_id,
                    detail=f"No visits for {minutes_idle}+ minutes",
                )
            )
    return anomalies


def detect_anomalies(db: Database, store_id: str) -> List[Anomaly]:
    """Run all anomaly detectors for a store."""
    events = db.fetch_events_for_store(store_id, exclude_staff=True)
    if not events:
        stores = db.list_store_ids()
        if store_id not in stores:
            raise HTTPException(status_code=404, detail=f"Store not found: {store_id}")
        return []

    findings: List[Anomaly] = []

    queue_spike = detect_queue_spike(events)
    if queue_spike:
        findings.append(queue_spike)

    conversion_drop = detect_conversion_drop(db, store_id)
    if conversion_drop:
        findings.append(conversion_drop)

    findings.extend(detect_dead_zones(events))
    return findings


@router.get("/{store_id}/anomalies", response_model=ApiResponse)
def get_store_anomalies(
    store_id: str,
    db: Database = Depends(get_database),
) -> ApiResponse:
    """Return detected operational anomalies for a store."""
    anomalies = detect_anomalies(db, store_id)
    return ApiResponse(data=anomalies)
