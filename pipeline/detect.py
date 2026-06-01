"""
Person detection entry point for the retail store intelligence pipeline.

Loads CCTV footage with OpenCV, runs YOLOv8 person detection on every frame,
and forwards structured detections to the tracking layer.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import cv2
import numpy as np
from ultralytics import YOLO

from pipeline.classifiers import ReIdentifier, StaffClassifier
from pipeline.emit import (
    EventMetadata,
    EventType,
    build_event,
    save_event_json,
)
from pipeline.models import BBox, Detection, FrameDetections, PERSON_CLASS_ID
from pipeline.queue import QueueEstimator
from pipeline.tracker import TrackManager, TrackRecord
from pipeline.zones import StoreZone, ZoneMapper

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "yolov8n.pt"


@dataclass
class PipelineConfig:
    """Runtime configuration for the detection pipeline."""

    video_path: Path
    output_path: Path = Path("output/events.jsonl")
    model_path: str = DEFAULT_MODEL
    camera_id: str = "CAM_ENTRY_01"
    store_id: str = "STORE_BLR_002"
    confidence_threshold: float = 0.35
    device: str = "cpu"
    max_frames: Optional[int] = None


class PersonDetector:
    """YOLOv8-based person detector."""

    def __init__(self, model_path: str = DEFAULT_MODEL, device: str = "cpu") -> None:
        """
        Load YOLOv8 weights for inference.

        Args:
            model_path: Path or model name understood by Ultralytics.
            device: Inference device, e.g. 'cpu' or '0' for GPU.
        """
        logger.info("loading YOLOv8 model: %s on device=%s", model_path, device)
        self.model = YOLO(model_path)
        self.device = device

    def detect_frame(
        self,
        frame: np.ndarray,
        frame_id: int,
        timestamp: float,
        confidence_threshold: float = 0.35,
    ) -> FrameDetections:
        """
        Run person detection on a single frame.

        Args:
            frame: BGR image array from OpenCV.
            frame_id: Monotonic frame index.
            timestamp: Video timestamp in seconds.
            confidence_threshold: Minimum detection confidence.

        Returns:
            FrameDetections with bounding boxes and confidence scores.
        """
        results = self.model.predict(
            source=frame,
            classes=[PERSON_CLASS_ID],
            conf=confidence_threshold,
            verbose=False,
            device=self.device,
        )

        frame_detections = FrameDetections(frame_id=frame_id, timestamp=timestamp)
        if not results:
            return frame_detections

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return frame_detections

        for box in boxes:
            xyxy = box.xyxy[0].tolist()
            bbox: BBox = (float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3]))
            confidence = float(box.conf[0])
            detection = Detection(bbox=bbox, confidence=confidence)
            frame_detections.detections.append(detection)
            frame_detections.boxes.append(bbox)
            frame_detections.confidences.append(confidence)

        logger.debug(
            "frame_id=%s detections=%s timestamp=%.3f",
            frame_id,
            len(frame_detections.detections),
            timestamp,
        )
        return frame_detections


def iter_video_frames(
    video_path: Path,
    max_frames: Optional[int] = None,
) -> Iterator[Tuple[int, float, np.ndarray]]:
    """
    Yield frames from a video file.

    Args:
        video_path: Path to input video.
        max_frames: Optional cap on frames to process.

    Yields:
        Tuples of (frame_id, timestamp_seconds, bgr_frame).
    """
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frame_id = 0

    try:
        while True:
            if max_frames is not None and frame_id >= max_frames:
                break

            success, frame = capture.read()
            if not success:
                break

            timestamp = frame_id / fps
            yield frame_id, timestamp, frame
            frame_id += 1
    finally:
        capture.release()


@dataclass
class SessionState:
    """Per-track session bookkeeping for event generation."""

    entered: bool = False
    in_queue: bool = False
    previous_zone: Optional[str] = None
    zone_enter_frame: Optional[int] = None
    zone_enter_timestamp: Optional[float] = None
    session_seq: int = 0
    has_exited: bool = False


class EventEngine:
    """
    Converts track updates into behavioral events using zone and queue logic.
    """

    def __init__(
        self,
        config: PipelineConfig,
        zone_mapper: ZoneMapper,
        queue_estimator: QueueEstimator,
        staff_classifier: StaffClassifier,
        reidentifier: ReIdentifier,
    ) -> None:
        self.config = config
        self.zone_mapper = zone_mapper
        self.queue_estimator = queue_estimator
        self.staff_classifier = staff_classifier
        self.reidentifier = reidentifier
        self._session_state: dict[int, SessionState] = {}

    def process_tracks(
        self,
        frame: np.ndarray,
        frame_id: int,
        timestamp: float,
        tracks: dict[int, TrackRecord],
        track_manager: TrackManager,
    ) -> List:
        """
        Generate behavioral events for updated tracks in the current frame.

        Returns:
            List of StoreEvent instances emitted this frame.
        """
        from pipeline.emit import StoreEvent

        events: List[StoreEvent] = []
        fps_estimate = 30.0

        for track_id, record in tracks.items():
            bbox = record.latest_bbox
            if bbox is None:
                continue

            zone = self.zone_mapper.map_bbox_to_zone(bbox)
            sku_zone = self.zone_mapper.map_bbox_to_sku_zone(bbox)
            zone_id = None if zone == StoreZone.UNKNOWN else zone.value
            track_manager.update_zone(track_id, zone_id)

            is_staff = self.staff_classifier.classify(track_id, bbox, frame)
            if zone == StoreZone.CASH_COUNTER and is_staff:
                self.staff_classifier.register_staff_track(track_id)

            self.queue_estimator.update(track_id, zone, is_staff)
            queue_depth = self.queue_estimator.estimate_depth()
            visitor_id = self.reidentifier.resolve_visitor_id(track_id, bbox, frame)
            session = self._session_state.setdefault(track_id, SessionState())
            confidence = record.confidence_history[-1] if record.confidence_history else 0.0

            metadata = EventMetadata(
                queue_depth=queue_depth,
                sku_zone=sku_zone,
                session_seq=session.session_seq,
            )

            if zone == StoreZone.ENTRANCE and not session.entered:
                session.entered = True
                session.session_seq += 1
                events.append(
                    build_event(
                        camera_id=self.config.camera_id,
                        store_id=self.config.store_id,
                        visitor_id=visitor_id,
                        event_type=EventType.ENTRY,
                        timestamp=self._iso_from_video_time(timestamp),
                        zone_id=StoreZone.ENTRANCE.value,
                        is_staff=is_staff,
                        confidence=confidence,
                        metadata=metadata,
                    )
                )

            if session.previous_zone != zone_id:
                if session.previous_zone is not None and session.zone_enter_timestamp is not None:
                    dwell_ms = int((timestamp - session.zone_enter_timestamp) * 1000)
                    events.append(
                        build_event(
                            camera_id=self.config.camera_id,
                            store_id=self.config.store_id,
                            visitor_id=visitor_id,
                            event_type=EventType.ZONE_EXIT,
                            timestamp=self._iso_from_video_time(timestamp),
                            zone_id=session.previous_zone,
                            dwell_ms=dwell_ms,
                            is_staff=is_staff,
                            confidence=confidence,
                            metadata=metadata,
                        )
                    )

                if zone_id is not None:
                    events.append(
                        build_event(
                            camera_id=self.config.camera_id,
                            store_id=self.config.store_id,
                            visitor_id=visitor_id,
                            event_type=EventType.ZONE_ENTER,
                            timestamp=self._iso_from_video_time(timestamp),
                            zone_id=zone_id,
                            is_staff=is_staff,
                            confidence=confidence,
                            metadata=metadata,
                        )
                    )
                    session.zone_enter_frame = frame_id
                    session.zone_enter_timestamp = timestamp

                session.previous_zone = zone_id

            if zone == StoreZone.BILLING_QUEUE and not session.in_queue and not is_staff:
                session.in_queue = True
                events.append(
                    build_event(
                        camera_id=self.config.camera_id,
                        store_id=self.config.store_id,
                        visitor_id=visitor_id,
                        event_type=EventType.BILLING_QUEUE_JOIN,
                        timestamp=self._iso_from_video_time(timestamp),
                        zone_id=StoreZone.BILLING_QUEUE.value,
                        is_staff=is_staff,
                        confidence=confidence,
                        metadata=metadata,
                    )
                )
            elif session.in_queue and zone != StoreZone.BILLING_QUEUE:
                session.in_queue = False
                events.append(
                    build_event(
                        camera_id=self.config.camera_id,
                        store_id=self.config.store_id,
                        visitor_id=visitor_id,
                        event_type=EventType.BILLING_QUEUE_ABANDON,
                        timestamp=self._iso_from_video_time(timestamp),
                        zone_id=StoreZone.BILLING_QUEUE.value,
                        is_staff=is_staff,
                        confidence=confidence,
                        metadata=metadata,
                    )
                )

            if (
                session.zone_enter_timestamp is not None
                and frame_id > 0
                and frame_id % int(max(fps_estimate, 1)) == 0
                and zone_id is not None
            ):
                dwell_ms = int((timestamp - session.zone_enter_timestamp) * 1000)
                if dwell_ms > 0:
                    events.append(
                        build_event(
                            camera_id=self.config.camera_id,
                            store_id=self.config.store_id,
                            visitor_id=visitor_id,
                            event_type=EventType.ZONE_DWELL,
                            timestamp=self._iso_from_video_time(timestamp),
                            zone_id=zone_id,
                            dwell_ms=dwell_ms,
                            is_staff=is_staff,
                            confidence=confidence,
                            metadata=metadata,
                        )
                    )

        return events

    @staticmethod
    def _iso_from_video_time(timestamp_seconds: float) -> str:
        """Convert video-relative seconds to an ISO-8601 UTC timestamp."""
        from pipeline.emit import utc_timestamp_iso
        from datetime import datetime, timedelta, timezone

        base = datetime.now(timezone.utc) - timedelta(seconds=timestamp_seconds)
        return utc_timestamp_iso(base + timedelta(seconds=timestamp_seconds))


def run_pipeline(config: PipelineConfig, *, clear_output: bool = True) -> Path:
    """
    Execute the full detect → track → emit pipeline on a video file.

    Args:
        config: Pipeline runtime configuration.
        clear_output: When True, delete existing output before writing.

    Returns:
        Path to the JSONL events output file.
    """
    if not config.video_path.exists():
        raise FileNotFoundError(f"Video not found: {config.video_path}")

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    if clear_output and config.output_path.exists():
        config.output_path.unlink()

    detector = PersonDetector(model_path=config.model_path, device=config.device)
    track_manager = TrackManager()
    zone_mapper = ZoneMapper()
    queue_estimator = QueueEstimator()
    staff_classifier = StaffClassifier()
    reidentifier = ReIdentifier()
    event_engine = EventEngine(
        config=config,
        zone_mapper=zone_mapper,
        queue_estimator=queue_estimator,
        staff_classifier=staff_classifier,
        reidentifier=reidentifier,
    )

    total_events = 0
    start = time.perf_counter()

    for frame_id, timestamp, frame in iter_video_frames(config.video_path, config.max_frames):
        if frame_id == 0:
            zone_mapper.frame_width = frame.shape[1]
            zone_mapper.frame_height = frame.shape[0]

        detections = detector.detect_frame(
            frame=frame,
            frame_id=frame_id,
            timestamp=timestamp,
            confidence_threshold=config.confidence_threshold,
        )
        updated_tracks = track_manager.update_tracks(detections)
        events = event_engine.process_tracks(
            frame=frame,
            frame_id=frame_id,
            timestamp=timestamp,
            tracks=updated_tracks,
            track_manager=track_manager,
        )

        for event in events:
            save_event_json(event, config.output_path, append=True)
            total_events += 1

        if frame_id > 0 and frame_id % 100 == 0:
            logger.info(
                "progress frame_id=%s tracks=%s events=%s",
                frame_id,
                len(track_manager.tracks),
                total_events,
            )

    elapsed = time.perf_counter() - start
    logger.info(
        "pipeline complete video=%s events=%s elapsed=%.2fs output=%s",
        config.video_path,
        total_events,
        elapsed,
        config.output_path,
    )
    return config.output_path


def configure_logging(verbose: bool = False) -> None:
    """Configure root logger for CLI execution."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args(argv: Optional[List[str]] = None) -> PipelineConfig:
    """Parse command-line arguments into pipeline configuration."""
    parser = argparse.ArgumentParser(
        description="Retail store intelligence person detection pipeline",
    )
    parser.add_argument("video_path", type=Path, help="Path to input CCTV video")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/events.jsonl"),
        help="JSONL output path (default: output/events.jsonl)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="YOLOv8 model path or name")
    parser.add_argument("--camera-id", default="CAM_ENTRY_01", help="Camera identifier")
    parser.add_argument("--store-id", default="STORE_BLR_002", help="Store identifier")
    parser.add_argument("--conf", type=float, default=0.35, help="Detection confidence threshold")
    parser.add_argument("--device", default="cpu", help="Inference device (cpu, 0, 1, ...)")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional frame limit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    configure_logging(verbose=args.verbose)
    return PipelineConfig(
        video_path=args.video_path,
        output_path=args.output,
        model_path=args.model,
        camera_id=args.camera_id,
        store_id=args.store_id,
        confidence_threshold=args.conf,
        device=args.device,
        max_frames=args.max_frames,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    try:
        config = parse_args(argv)
        run_pipeline(config)
        return 0
    except Exception:
        logger.exception("pipeline failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
