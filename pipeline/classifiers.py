"""
Placeholder modules for staff classification and visitor re-identification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class StaffClassifier:
    """
    Placeholder staff vs visitor classifier.

    Production implementation should use uniform detection, badge recognition,
    or a dedicated classifier trained on store staff imagery.
    """

    staff_track_ids: set[int] = field(default_factory=set)

    def classify(self, track_id: int, bbox: tuple[float, float, float, float], frame: Any) -> bool:
        """
        Determine whether a tracked person is store staff.

        Args:
            track_id: Stable tracker ID.
            bbox: Current bounding box (x1, y1, x2, y2).
            frame: Raw BGR frame (unused in placeholder).

        Returns:
            True if classified as staff, False otherwise.
        """
        _ = frame
        is_staff = track_id in self.staff_track_ids
        logger.debug("staff classification track_id=%s is_staff=%s", track_id, is_staff)
        return is_staff

    def register_staff_track(self, track_id: int) -> None:
        """Manually mark a track as staff (e.g., from fixed counter position)."""
        self.staff_track_ids.add(track_id)


@dataclass
class ReIdentifier:
    """
    Placeholder visitor re-identification module.

    Production implementation should embed appearance features and match
    against a gallery to produce stable visitor_id across sessions/cameras.
    """

    _visitor_map: Dict[int, str] = field(default_factory=dict)
    _next_visitor_seq: int = 1

    def resolve_visitor_id(
        self,
        track_id: int,
        bbox: tuple[float, float, float, float],
        frame: Any,
    ) -> str:
        """
        Resolve or assign a visitor_id for a track.

        Args:
            track_id: Stable tracker ID within the current video session.
            bbox: Current bounding box.
            frame: Raw BGR frame (unused in placeholder).

        Returns:
            Visitor identifier string, e.g. VIS_000001.
        """
        _ = bbox, frame
        if track_id not in self._visitor_map:
            visitor_id = f"VIS_{self._next_visitor_seq:06d}"
            self._visitor_map[track_id] = visitor_id
            self._next_visitor_seq += 1
            logger.info("assigned visitor_id=%s to track_id=%s", visitor_id, track_id)
        return self._visitor_map[track_id]

    def lookup_visitor_id(self, track_id: int) -> Optional[str]:
        """Return existing visitor_id for a track, if known."""
        return self._visitor_map.get(track_id)
