"""
Multi-object tracking with ByteTrack for stable person track IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import supervision as sv

from pipeline.models import FrameDetections

logger = logging.getLogger(__name__)

TrackState = Dict[str, object]
BBox = Tuple[float, float, float, float]


@dataclass
class TrackRecord:
    """Internal state for a single tracked person."""

    track_id: int
    bbox_history: List[BBox] = field(default_factory=list)
    confidence_history: List[float] = field(default_factory=list)
    first_seen_frame: int = 0
    last_seen_frame: int = 0
    first_seen_timestamp: float = 0.0
    last_seen_timestamp: float = 0.0
    zone_history: List[Optional[str]] = field(default_factory=list)
    current_zone: Optional[str] = None

    @property
    def first_seen(self) -> Tuple[int, float]:
        """Return (frame_id, timestamp) when the track was first observed."""
        return self.first_seen_frame, self.first_seen_timestamp

    @property
    def last_seen(self) -> Tuple[int, float]:
        """Return (frame_id, timestamp) when the track was last observed."""
        return self.last_seen_frame, self.last_seen_timestamp

    @property
    def latest_bbox(self) -> Optional[BBox]:
        """Return the most recent bounding box, if any."""
        return self.bbox_history[-1] if self.bbox_history else None

    def to_dict(self) -> TrackState:
        """Serialize track state for logging or downstream consumers."""
        return {
            "track_id": self.track_id,
            "bbox_history": self.bbox_history,
            "confidence_history": self.confidence_history,
            "first_seen": {"frame_id": self.first_seen_frame, "timestamp": self.first_seen_timestamp},
            "last_seen": {"frame_id": self.last_seen_frame, "timestamp": self.last_seen_timestamp},
            "zone_history": self.zone_history,
            "current_zone": self.current_zone,
        }


class TrackManager:
    """
    Maintains stable person tracks using ByteTrack.

    Accepts per-frame detections from detect.py and returns updated track
    records keyed by track_id.
    """

    def __init__(
        self,
        track_activation_threshold: float = 0.25,
        lost_track_buffer: int = 30,
        minimum_matching_threshold: float = 0.8,
        frame_rate: int = 30,
    ) -> None:
        """
        Initialize ByteTrack-backed track manager.

        Args:
            track_activation_threshold: Minimum confidence to start a track.
            lost_track_buffer: Frames to keep lost tracks before removal.
            minimum_matching_threshold: IoU threshold for association.
            frame_rate: Source video frame rate for tracker timing.
        """
        self._byte_tracker = sv.ByteTrack(
            track_activation_threshold=track_activation_threshold,
            lost_track_buffer=lost_track_buffer,
            minimum_matching_threshold=minimum_matching_threshold,
            frame_rate=frame_rate,
        )
        self._tracks: Dict[int, TrackRecord] = {}

    @property
    def tracks(self) -> Dict[int, TrackRecord]:
        """Return all active and recently seen track records."""
        return dict(self._tracks)

    def update_tracks(self, detections: FrameDetections) -> Dict[int, TrackRecord]:
        """
        Associate detections with existing tracks and update track state.

        Args:
            detections: Frame-level detection payload from detect.py.

        Returns:
            Mapping of track_id to TrackRecord for tracks updated this frame.
        """
        sv_detections = self._to_supervision_detections(detections)
        tracked = self._byte_tracker.update_with_detections(sv_detections)

        updated: Dict[int, TrackRecord] = {}
        if tracked.tracker_id is None or len(tracked) == 0:
            logger.debug("frame_id=%s no tracked objects", detections.frame_id)
            return updated

        for idx in range(len(tracked)):
            track_id = int(tracked.tracker_id[idx])  # type: ignore[index]
            bbox = self._xyxy_from_supervision(tracked, idx)
            confidence = float(tracked.confidence[idx]) if tracked.confidence is not None else 0.0

            record = self._tracks.get(track_id)
            if record is None:
                record = TrackRecord(
                    track_id=track_id,
                    first_seen_frame=detections.frame_id,
                    first_seen_timestamp=detections.timestamp,
                    last_seen_frame=detections.frame_id,
                    last_seen_timestamp=detections.timestamp,
                )
                self._tracks[track_id] = record
                logger.info(
                    "new track_id=%s at frame_id=%s timestamp=%.3f",
                    track_id,
                    detections.frame_id,
                    detections.timestamp,
                )
            else:
                record.last_seen_frame = detections.frame_id
                record.last_seen_timestamp = detections.timestamp

            record.bbox_history.append(bbox)
            record.confidence_history.append(confidence)
            updated[track_id] = record

        return updated

    def update_zone(self, track_id: int, zone_id: Optional[str]) -> None:
        """
        Append a zone observation to a track's history.

        Args:
            track_id: Stable tracker ID.
            zone_id: Resolved zone identifier, or None if unknown.
        """
        record = self._tracks.get(track_id)
        if record is None:
            logger.warning("update_zone called for unknown track_id=%s", track_id)
            return
        record.zone_history.append(zone_id)
        record.current_zone = zone_id

    def get_track(self, track_id: int) -> Optional[TrackRecord]:
        """Return track record for a given ID, if it exists."""
        return self._tracks.get(track_id)

    @staticmethod
    def _to_supervision_detections(detections: FrameDetections) -> sv.Detections:
        """Convert pipeline detections to supervision Detections."""
        if not detections.boxes:
            return sv.Detections.empty()

        xyxy = np.array(detections.boxes, dtype=np.float32)
        confidence = np.array(detections.confidences, dtype=np.float32)
        class_id = np.zeros(len(detections.boxes), dtype=int)
        return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)

    @staticmethod
    def _xyxy_from_supervision(tracked: sv.Detections, index: int) -> BBox:
        """Extract xyxy bbox tuple from supervision detections."""
        x1, y1, x2, y2 = tracked.xyxy[index]
        return float(x1), float(y1), float(x2), float(y2)
