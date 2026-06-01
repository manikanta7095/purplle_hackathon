"""
Session-based conversion funnel analytics.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.database import Database, SessionRow
from app.ingestion import get_database
from app.models import ApiResponse, FunnelResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stores", tags=["funnel"])


def dropoff_between(upper: int, lower: int) -> float:
    """Percentage lost between consecutive funnel stages."""
    if upper == 0:
        return 0.0
    lost = max(0, upper - lower)
    return round((lost / upper) * 100.0, 2)


def compute_funnel(sessions: List[SessionRow]) -> FunnelResponse:
    """
    Build the Entry → Zone Visit → Billing Queue → Purchase funnel.

    Counts are session-based so re-entry does not double-count visitors.
    """
    entry_count = len(sessions)
    zone_visit_count = sum(1 for session in sessions if session.has_zone_visit)
    billing_queue_count = sum(1 for session in sessions if session.has_billing_queue)
    purchase_count = sum(1 for session in sessions if session.has_purchase)
    purchased_from_queue = sum(
        1 for session in sessions if session.has_billing_queue and session.has_purchase
    )

    dropoff: Dict[str, float] = {}
    if entry_count > 0:
        dropoff["entry_to_zone_visit"] = dropoff_between(entry_count, zone_visit_count)
        dropoff["zone_visit_to_billing"] = dropoff_between(zone_visit_count, billing_queue_count)
        dropoff["billing_to_purchase"] = dropoff_between(
            billing_queue_count,
            purchased_from_queue,
        )
        dropoff["entry_to_purchase"] = dropoff_between(entry_count, purchase_count)

    return FunnelResponse(
        entry_count=entry_count,
        zone_visit_count=zone_visit_count,
        billing_queue_count=billing_queue_count,
        purchase_count=purchase_count,
        dropoff_percentages=dropoff,
    )


@router.get("/{store_id}/funnel", response_model=ApiResponse)
def get_store_funnel(
    store_id: str,
    db: Database = Depends(get_database),
) -> ApiResponse:
    """Return session-based funnel metrics for a store."""
    sessions = db.fetch_sessions_for_store(store_id, exclude_staff=True)
    if not sessions:
        stores = db.list_store_ids()
        if store_id not in stores:
            raise HTTPException(status_code=404, detail=f"Store not found: {store_id}")

    funnel = compute_funnel(sessions)
    return ApiResponse(data=funnel)
