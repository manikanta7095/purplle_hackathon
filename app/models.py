"""
Pydantic models and domain types for the store intelligence API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    """Behavioral event types emitted by the detection pipeline."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"
    PURCHASE = "PURCHASE"


class AnomalySeverity(str, Enum):
    """Severity levels for detected anomalies."""

    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AnomalyType(str, Enum):
    """Supported anomaly categories."""

    QUEUE_SPIKE = "QUEUE_SPIKE"
    CONVERSION_DROP = "CONVERSION_DROP"
    DEAD_ZONE = "DEAD_ZONE"


# Zones excluded from retail SKU heatmap / zone-visit funnel stage.
SYSTEM_ZONES = frozenset(
    {
        "ZONE_ENTRANCE",
        "ENTRANCE",
        "BILLING_QUEUE",
        "CASH_COUNTER",
        "UNKNOWN",
    }
)

PURCHASE_ZONES = frozenset({"CASH_COUNTER"})


class EventMetadata(BaseModel):
    """Nested metadata block on store events."""

    model_config = ConfigDict(extra="ignore")

    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


class StoreEvent(BaseModel):
    """Canonical event schema accepted by the ingestion endpoint."""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    camera_id: str
    store_id: str
    visitor_id: str
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(default=0.0, ge=0.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        """Ensure event_id is a valid UUIDv4."""
        parsed = uuid.UUID(str(value), version=4)
        return str(parsed)


class IngestRequest(BaseModel):
    """Batch event ingestion payload (per-event validation at ingest time)."""

    events: List[Dict[str, Any]] = Field(..., min_length=1, max_length=500)


class IngestError(BaseModel):
    """Per-event ingestion failure detail."""

    event_id: Optional[str] = None
    index: int
    message: str


class IngestResponse(BaseModel):
    """Batch ingestion result summary."""

    accepted: int
    rejected: int
    duplicates: int
    errors: List[IngestError]


class StoreMetrics(BaseModel):
    """Real-time store performance metrics."""

    unique_visitors: int = 0
    conversion_rate: float = 0.0
    avg_dwell_per_zone: Dict[str, float] = Field(default_factory=dict)
    avg_queue_depth: float = 0.0
    abandonment_rate: float = 0.0


class FunnelResponse(BaseModel):
    """Session-based conversion funnel."""

    entry_count: int = 0
    zone_visit_count: int = 0
    billing_queue_count: int = 0
    purchase_count: int = 0
    dropoff_percentages: Dict[str, float] = Field(default_factory=dict)


class HeatmapZone(BaseModel):
    """Single zone heatmap entry."""

    zone_id: str
    visit_frequency: int = 0
    avg_dwell_ms: float = 0.0
    score: float = 0.0


class HeatmapResponse(BaseModel):
    """Normalized zone engagement heatmap."""

    zones: List[HeatmapZone] = Field(default_factory=list)
    data_confidence: bool = True


class Anomaly(BaseModel):
    """Detected operational anomaly."""

    type: AnomalyType
    severity: AnomalySeverity
    suggested_action: str
    zone_id: Optional[str] = None
    detail: Optional[str] = None


class HealthResponse(BaseModel):
    """Service health snapshot."""

    status: str = "healthy"
    last_event_timestamp: Optional[str] = None
    stores: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ApiResponse(BaseModel):
    """Consistent envelope for successful JSON responses."""

    success: bool = True
    data: Any
