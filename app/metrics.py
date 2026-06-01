"""
Real-time store metrics computation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database, EventRow, SessionRow
from app.ingestion import get_database
from app.models import (
    ApiResponse,
    EventType,
    HeatmapResponse,
    HeatmapZone,
    StoreMetrics,
    SYSTEM_ZONES,
)

MIN_SESSIONS_FOR_CONFIDENCE = 20

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stores", tags=["metrics"])


def safe_divide(numerator: float, denominator: float) -> float:
    """Return ratio or 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_avg_dwell_per_zone(events: List[EventRow]) -> Dict[str, float]:
    """
    Compute average dwell time per retail zone from ZONE_DWELL and ZONE_EXIT events.
    """
    dwell_totals: Dict[str, int] = defaultdict(int)
    dwell_counts: Dict[str, int] = defaultdict(int)

    for event in events:
        if event.is_staff or not event.zone_id or event.zone_id in SYSTEM_ZONES:
            continue
        if event.event_type in {EventType.ZONE_DWELL.value, EventType.ZONE_EXIT.value}:
            if event.dwell_ms > 0:
                dwell_totals[event.zone_id] += event.dwell_ms
                dwell_counts[event.zone_id] += 1

    return {
        zone: dwell_totals[zone] / dwell_counts[zone]
        for zone in dwell_totals
        if dwell_counts[zone] > 0
    }


def compute_avg_queue_depth(events: List[EventRow]) -> float:
    """Average queue depth from event metadata where available."""
    depths: List[int] = []
    for event in events:
        if event.is_staff:
            continue
        depth = event.metadata.get("queue_depth")
        if depth is not None:
            depths.append(int(depth))
    if not depths:
        return 0.0
    return sum(depths) / len(depths)


def compute_metrics_from_sessions(
    sessions: List[SessionRow],
    events: List[EventRow],
) -> StoreMetrics:
    """Derive headline KPIs from visitor sessions and raw events."""
    unique_visitors = len({session.visitor_id for session in sessions})
    purchases = sum(1 for session in sessions if session.has_purchase)
    queue_joiners = sum(1 for session in sessions if session.has_billing_queue)
    abandoned = sum(
        1
        for session in sessions
        if session.has_billing_queue and not session.has_purchase
    )

    return StoreMetrics(
        unique_visitors=unique_visitors,
        conversion_rate=safe_divide(purchases, unique_visitors),
        avg_dwell_per_zone=compute_avg_dwell_per_zone(events),
        avg_queue_depth=compute_avg_queue_depth(events),
        abandonment_rate=safe_divide(abandoned, queue_joiners),
    )


def compute_store_metrics(db: Database, store_id: str) -> StoreMetrics:
    """Compute real-time metrics for a store from latest ingested data."""
    sessions = db.fetch_sessions_for_store(store_id, exclude_staff=True)
    events = db.fetch_events_for_store(store_id, exclude_staff=True)

    if not sessions and not events:
        stores = db.list_store_ids()
        if store_id not in stores:
            return StoreMetrics()

    metrics = compute_metrics_from_sessions(sessions, events)
    db.upsert_metric_cache(store_id, "conversion_rate", metrics.conversion_rate)
    db.upsert_metric_cache(store_id, "unique_visitors", float(metrics.unique_visitors))
    return metrics


def compute_daily_conversion_rates(
    db: Database,
    store_id: str,
    *,
    days: int = 7,
) -> List[float]:
    """
    Compute daily conversion rates for the past N days.

    Used by anomaly detection for rolling baselines.
    """
    now = datetime.now(timezone.utc)
    rates: List[float] = []

    for offset in range(days):
        day_start = (now - timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        sessions = db.fetch_sessions_for_store(
            store_id,
            exclude_staff=True,
            since=day_start.isoformat(),
        )
        day_sessions = [
            session
            for session in sessions
            if day_start.isoformat() <= session.started_at < day_end.isoformat()
        ]
        purchases = sum(1 for session in day_sessions if session.has_purchase)
        rates.append(safe_divide(purchases, len(day_sessions)))

    return rates


def compute_heatmap(
    events: List[EventRow],
    session_count: int,
) -> HeatmapResponse:
    """
    Build zone heatmap with scores normalized to 0–100.

    Score combines visit frequency and average dwell relative to the busiest zone.
    """
    visit_counts: Dict[str, int] = defaultdict(int)
    dwell_totals: Dict[str, int] = defaultdict(int)
    dwell_samples: Dict[str, int] = defaultdict(int)

    for event in events:
        if event.is_staff or not event.zone_id or event.zone_id in SYSTEM_ZONES:
            continue

        if event.event_type == EventType.ZONE_ENTER.value:
            visit_counts[event.zone_id] += 1

        if event.event_type in {EventType.ZONE_DWELL.value, EventType.ZONE_EXIT.value}:
            if event.dwell_ms > 0:
                dwell_totals[event.zone_id] += event.dwell_ms
                dwell_samples[event.zone_id] += 1

    if not visit_counts:
        return HeatmapResponse(
            zones=[],
            data_confidence=session_count >= MIN_SESSIONS_FOR_CONFIDENCE,
        )

    max_visits = max(visit_counts.values())
    max_dwell = max(
        (dwell_totals[z] / dwell_samples[z] for z in dwell_totals if dwell_samples[z] > 0),
        default=1.0,
    )

    zones: List[HeatmapZone] = []
    for zone_id in sorted(visit_counts.keys()):
        visits = visit_counts[zone_id]
        avg_dwell = (
            dwell_totals[zone_id] / dwell_samples[zone_id]
            if dwell_samples[zone_id] > 0
            else 0.0
        )
        visit_norm = (visits / max_visits) * 50 if max_visits else 0.0
        dwell_score = (avg_dwell / max_dwell) * 50 if max_dwell else 0.0
        score = round(min(100.0, visit_norm + dwell_score), 2)

        zones.append(
            HeatmapZone(
                zone_id=zone_id,
                visit_frequency=visits,
                avg_dwell_ms=round(avg_dwell, 2),
                score=score,
            )
        )

    zones.sort(key=lambda zone: zone.score, reverse=True)
    return HeatmapResponse(
        zones=zones,
        data_confidence=session_count >= MIN_SESSIONS_FOR_CONFIDENCE,
    )


@router.get("/{store_id}/metrics", response_model=ApiResponse)
def get_store_metrics(
    store_id: str,
    db: Database = Depends(get_database),
) -> ApiResponse:
    """Return real-time store KPIs excluding staff activity."""
    metrics = compute_store_metrics(db, store_id)
    return ApiResponse(data=metrics)


@router.get("/{store_id}/heatmap", response_model=ApiResponse)
def get_store_heatmap(
    store_id: str,
    db: Database = Depends(get_database),
) -> ApiResponse:
    """Return normalized zone engagement heatmap for a store."""
    events = db.fetch_events_for_store(store_id, exclude_staff=True)
    sessions = db.fetch_sessions_for_store(store_id, exclude_staff=True)

    if not events and not sessions:
        stores = db.list_store_ids()
        if store_id not in stores:
            raise HTTPException(status_code=404, detail=f"Store not found: {store_id}")

    heatmap = compute_heatmap(events, len(sessions))
    return ApiResponse(data=heatmap)
