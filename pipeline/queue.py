"""
Billing queue depth estimation utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, Set

from pipeline.zones import StoreZone

logger = logging.getLogger(__name__)


@dataclass
class QueueEstimator:
    """
    Placeholder billing queue depth estimator.

    Tracks how many non-staff visitors are present in the billing queue zone.
    """

    queue_zone: StoreZone = StoreZone.BILLING_QUEUE
    _tracks_in_queue: Set[int] = field(default_factory=set)

    def update(
        self,
        track_id: int,
        current_zone: StoreZone,
        is_staff: bool,
    ) -> None:
        """Update queue membership for a track based on its current zone."""
        if is_staff:
            self._tracks_in_queue.discard(track_id)
            return

        if current_zone == self.queue_zone:
            self._tracks_in_queue.add(track_id)
        else:
            self._tracks_in_queue.discard(track_id)

    def estimate_depth(self) -> int:
        """
        Estimate current billing queue depth.

        Returns:
            Number of non-staff visitors in the billing queue zone.
        """
        depth = len(self._tracks_in_queue)
        logger.debug("billing queue depth=%s", depth)
        return depth

    def active_queue_tracks(self) -> Iterable[int]:
        """Return track IDs currently counted in the billing queue."""
        return frozenset(self._tracks_in_queue)
