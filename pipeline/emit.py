"""
Event schema utilities and JSONL emission for the store intelligence pipeline.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, TextIO, Union

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Supported behavioral event types."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


@dataclass
class EventMetadata:
    """Nested metadata block for store intelligence events."""

    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize metadata to a plain dictionary."""
        return {
            "queue_depth": self.queue_depth,
            "sku_zone": self.sku_zone,
            "session_seq": self.session_seq,
        }


@dataclass
class StoreEvent:
    """
    Canonical store intelligence event schema.

    All fields mirror the production event contract.
    """

    event_id: str
    camera_id: str
    store_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = 0.0
    metadata: EventMetadata = field(default_factory=EventMetadata)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to a JSON-compatible dictionary."""
        payload = asdict(self)
        payload["metadata"] = self.metadata.to_dict()
        return payload


def generate_event_id() -> str:
    """Generate a UUIDv4 event identifier."""
    return str(uuid.uuid4())


def utc_timestamp_iso(dt: Optional[datetime] = None) -> str:
    """Return an ISO-8601 UTC timestamp string."""
    moment = dt or datetime.now(timezone.utc)
    return moment.isoformat()


def validate_event(event: Union[StoreEvent, Dict[str, Any]]) -> bool:
    """
    Validate event structure before emission.

    Args:
        event: StoreEvent instance or raw dictionary.

    Returns:
        True if the event passes schema validation.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    data = event.to_dict() if isinstance(event, StoreEvent) else event

    required_fields = (
        "event_id",
        "camera_id",
        "store_id",
        "visitor_id",
        "event_type",
        "timestamp",
        "zone_id",
        "dwell_ms",
        "is_staff",
        "confidence",
        "metadata",
    )
    missing = [field_name for field_name in required_fields if field_name not in data]
    if missing:
        raise ValueError(f"Event missing required fields: {missing}")

    try:
        uuid.UUID(str(data["event_id"]), version=4)
    except ValueError as exc:
        raise ValueError(f"Invalid event_id (expected UUIDv4): {data['event_id']}") from exc

    try:
        EventType(str(data["event_type"]))
    except ValueError as exc:
        valid = ", ".join(e.value for e in EventType)
        raise ValueError(
            f"Invalid event_type '{data['event_type']}'. Must be one of: {valid}"
        ) from exc

    metadata = data["metadata"]
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dictionary")

    for meta_key in ("queue_depth", "sku_zone", "session_seq"):
        if meta_key not in metadata:
            raise ValueError(f"metadata missing required key: {meta_key}")

    if not isinstance(data["dwell_ms"], int) or data["dwell_ms"] < 0:
        raise ValueError("dwell_ms must be a non-negative integer")

    if not isinstance(data["confidence"], (int, float)):
        raise ValueError("confidence must be numeric")

    if not isinstance(data["is_staff"], bool):
        raise ValueError("is_staff must be a boolean")

    return True


def build_event(
    *,
    camera_id: str,
    store_id: str,
    visitor_id: str,
    event_type: EventType,
    timestamp: Optional[str] = None,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.0,
    metadata: Optional[EventMetadata] = None,
    event_id: Optional[str] = None,
) -> StoreEvent:
    """
    Construct a validated StoreEvent.

    Args:
        camera_id: Source camera identifier.
        store_id: Store identifier.
        visitor_id: Visitor identifier.
        event_type: Behavioral event type.
        timestamp: ISO-8601 UTC timestamp; generated if omitted.
        zone_id: Optional zone identifier.
        dwell_ms: Dwell duration in milliseconds.
        is_staff: Whether the subject is staff.
        confidence: Detection or inference confidence.
        metadata: Optional metadata block.
        event_id: Optional pre-generated event ID.

    Returns:
        Validated StoreEvent instance.
    """
    event = StoreEvent(
        event_id=event_id or generate_event_id(),
        camera_id=camera_id,
        store_id=store_id,
        visitor_id=visitor_id,
        event_type=event_type.value,
        timestamp=timestamp or utc_timestamp_iso(),
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=is_staff,
        confidence=confidence,
        metadata=metadata or EventMetadata(),
    )
    validate_event(event)
    return event


def emit_event(
    event: StoreEvent,
    sink: TextIO,
) -> None:
    """
    Validate and emit a single event as a JSON line.

    Args:
        event: Event to emit.
        sink: Writable text stream (file handle or stdout).
    """
    validate_event(event)
    line = json.dumps(event.to_dict(), separators=(",", ":"))
    sink.write(line + "\n")
    sink.flush()
    logger.debug("emitted event_id=%s type=%s", event.event_id, event.event_type)


def save_event_json(
    event: StoreEvent,
    output_path: Union[str, Path],
    append: bool = True,
) -> None:
    """
    Persist a single event to a newline-delimited JSON (JSONL) file.

    Args:
        event: Event to persist.
        output_path: Target JSONL file path.
        append: When True, append to the file; otherwise overwrite.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as handle:
        emit_event(event, handle)
    logger.info("saved event_id=%s to %s", event.event_id, path)
