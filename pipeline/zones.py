"""
Zone mapping utilities for Brigade Bangalore (F.O.H) store layout.

Zones are derived from the architectural floor plan (dimensions in mm).
Coordinates are normalized to [0, 1] relative to the camera view; calibrate
per camera during deployment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

BBox = Tuple[float, float, float, float]  # x1, y1, x2, y2 (pixel or normalized)


class StoreZone(str, Enum):
    """Named zones aligned with the Brigade Bangalore store layout."""

    ENTRANCE = "ZONE_ENTRANCE"
    EB_KOREAN = "EB_KOREAN"
    THE_FACE_SHOP = "THE_FACE_SHOP"
    GOOD_VIBES = "GOOD_VIBES"
    DERMDOC = "DERMDOC"
    MINIMALIST = "MINIMALIST"
    AQUALOGICA = "AQUALOGICA"
    LAKME_SKIN = "LAKME_SKIN"
    ACCESSORIES = "ACCESSORIES"
    MAYBELLINE = "MAYBELLINE"
    FACES_CANADA = "FACES_CANADA"
    LAKME = "LAKME"
    COLORBAR_SUGAR = "COLORBAR_SUGAR"
    SWISS_BEAUTY = "SWISS_BEAUTY"
    RENEE_NY_BAE = "RENEE_NY_BAE"
    ALPS_GOODNESS = "ALPS_GOODNESS"
    STREAX = "STREAX"
    FRAGRANCE_NAIL_GONDOLA = "FRAGRANCE_NAIL_GONDOLA"
    MAKEUP_UNIT = "MAKEUP_UNIT"
    CASH_COUNTER = "CASH_COUNTER"
    BILLING_QUEUE = "BILLING_QUEUE"
    PMU = "PMU"
    LED_PANEL = "LED_PANEL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ZonePolygon:
    """Axis-aligned bounding region for a store zone (normalized coords)."""

    zone_id: StoreZone
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    sku_zone: Optional[str] = None

    def contains_point(self, x: float, y: float) -> bool:
        """Return True if point (x, y) lies inside this zone."""
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max

    def contains_bbox_center(self, bbox: BBox) -> bool:
        """Return True if the center of bbox lies inside this zone."""
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        return self.contains_point(cx, cy)


# Placeholder polygons — calibrate against homography / camera calibration.
# Layout reference (mm, left-to-right): entrance | 2594 | gondola 500 | 1347 |
# makeup 900 | 2000 | cash counter. Vertical span between walls: 4020 mm.
BRIGADE_BANGALORE_ZONES: Tuple[ZonePolygon, ...] = (
    ZonePolygon(StoreZone.ENTRANCE, 0.00, 0.20, 0.12, 0.80),
    ZonePolygon(StoreZone.EB_KOREAN, 0.12, 0.00, 0.20, 0.18, "KOREAN_SKINCARE"),
    ZonePolygon(StoreZone.THE_FACE_SHOP, 0.20, 0.00, 0.28, 0.18, "SKINCARE"),
    ZonePolygon(StoreZone.GOOD_VIBES, 0.28, 0.00, 0.36, 0.18, "SKINCARE"),
    ZonePolygon(StoreZone.DERMDOC, 0.36, 0.00, 0.44, 0.18, "SKINCARE"),
    ZonePolygon(StoreZone.MINIMALIST, 0.44, 0.00, 0.52, 0.18, "MOISTURISER"),
    ZonePolygon(StoreZone.AQUALOGICA, 0.52, 0.00, 0.60, 0.18, "MOISTURISER"),
    ZonePolygon(StoreZone.LAKME_SKIN, 0.60, 0.00, 0.72, 0.18, "SKINCARE"),
    ZonePolygon(StoreZone.ACCESSORIES, 0.72, 0.00, 0.88, 0.18, "ACCESSORIES"),
    ZonePolygon(StoreZone.FRAGRANCE_NAIL_GONDOLA, 0.12, 0.28, 0.28, 0.50, "FRAGRANCE"),
    ZonePolygon(StoreZone.MAKEUP_UNIT, 0.42, 0.28, 0.58, 0.50, "MAKEUP"),
    ZonePolygon(StoreZone.MAYBELLINE, 0.12, 0.82, 0.24, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.FACES_CANADA, 0.24, 0.82, 0.36, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.LAKME, 0.36, 0.82, 0.48, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.COLORBAR_SUGAR, 0.48, 0.82, 0.58, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.SWISS_BEAUTY, 0.58, 0.82, 0.68, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.RENEE_NY_BAE, 0.68, 0.82, 0.78, 1.00, "MAKEUP"),
    ZonePolygon(StoreZone.ALPS_GOODNESS, 0.78, 0.82, 0.86, 1.00, "HAIRCARE"),
    ZonePolygon(StoreZone.STREAX, 0.86, 0.82, 0.94, 1.00, "HAIRCARE"),
    ZonePolygon(StoreZone.CASH_COUNTER, 0.82, 0.20, 0.98, 0.70),
    ZonePolygon(StoreZone.BILLING_QUEUE, 0.70, 0.20, 0.82, 0.70),
    ZonePolygon(StoreZone.PMU, 0.88, 0.72, 0.98, 0.95),
    ZonePolygon(StoreZone.LED_PANEL, 0.90, 0.10, 0.98, 0.20),
)


@dataclass
class ZoneMapper:
    """
    Maps bounding boxes to store zones.

    Placeholder implementation using normalized bbox centers. Replace with
    homography-based floor-plan projection for production accuracy.
    """

    zones: Sequence[ZonePolygon] = field(default_factory=lambda: BRIGADE_BANGALORE_ZONES)
    frame_width: int = 1920
    frame_height: int = 1080

    def map_bbox_to_zone(self, bbox: BBox) -> StoreZone:
        """
        Resolve the store zone for a detection bounding box.

        Args:
            bbox: Pixel-space bounding box (x1, y1, x2, y2).

        Returns:
            Matched StoreZone, or UNKNOWN if no zone contains the center.
        """
        norm_bbox = self._normalize_bbox(bbox)
        for zone in self.zones:
            if zone.contains_bbox_center(norm_bbox):
                logger.debug("bbox %s mapped to zone %s", bbox, zone.zone_id.value)
                return zone.zone_id
        return StoreZone.UNKNOWN

    def map_bbox_to_sku_zone(self, bbox: BBox) -> Optional[str]:
        """Return sku_zone label for a bbox, if the matched zone defines one."""
        norm_bbox = self._normalize_bbox(bbox)
        for zone in self.zones:
            if zone.contains_bbox_center(norm_bbox):
                return zone.sku_zone
        return None

    def _normalize_bbox(self, bbox: BBox) -> BBox:
        """Convert pixel bbox to normalized [0, 1] coordinates."""
        x1, y1, x2, y2 = bbox
        w = max(self.frame_width, 1)
        h = max(self.frame_height, 1)
        return (x1 / w, y1 / h, x2 / w, y2 / h)
