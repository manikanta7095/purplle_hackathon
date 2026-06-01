"""
Shared data models for the store intelligence pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

BBox = Tuple[float, float, float, float]
PERSON_CLASS_ID = 0


@dataclass
class Detection:
    """Single person detection within a frame."""

    bbox: BBox
    confidence: float
    class_id: int = PERSON_CLASS_ID


@dataclass
class FrameDetections:
    """Aggregated detections for one video frame."""

    frame_id: int
    timestamp: float
    boxes: List[BBox] = field(default_factory=list)
    confidences: List[float] = field(default_factory=list)
    detections: List[Detection] = field(default_factory=list)
