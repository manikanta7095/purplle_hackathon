"""
Batch runner — process all CCTV videos in a folder through the detection pipeline.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from pipeline.detect import PipelineConfig, configure_logging, run_pipeline

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v"}


def derive_camera_id(video_path: Path) -> str:
    """
    Derive a camera_id from a video filename.

    Examples:
        CAM 1.mp4  -> CAM_1
        cam-02.mp4 -> CAM_02
        entry_cam.mp4 -> ENTRY_CAM
    """
    stem = video_path.stem.strip()
    normalized = re.sub(r"[\s\-]+", "_", stem)
    normalized = re.sub(r"[^A-Za-z0-9_]", "", normalized)
    return normalized.upper() or "CAM_UNKNOWN"


def discover_videos(
    folder: Path,
    *,
    recursive: bool = True,
    extensions: Sequence[str] = tuple(VIDEO_EXTENSIONS),
) -> List[Path]:
    """
    Find video files under a folder, sorted by name.

    Args:
        folder: Root directory to scan.
        recursive: Search subdirectories when True.
        extensions: Allowed video file extensions (with leading dot).

    Returns:
        Sorted list of video file paths.
    """
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    ext_set = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    pattern = "**/*" if recursive else "*"
    videos = [
        path
        for path in folder.glob(pattern)
        if path.is_file() and path.suffix.lower() in ext_set
    ]
    return sorted(videos, key=lambda p: p.name.lower())


def append_file_contents(source: Path, destination: Path) -> int:
    """
    Append all lines from source JSONL into destination.

    Returns:
        Number of lines appended.
    """
    if not source.exists() or source.stat().st_size == 0:
        return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    line_count = 0
    with source.open("r", encoding="utf-8") as src, destination.open("a", encoding="utf-8") as dst:
        for line in src:
            if line.strip():
                dst.write(line if line.endswith("\n") else line + "\n")
                line_count += 1
    return line_count


def run_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    combined_output: Optional[Path] = None,
    store_id: str = "STORE_BLR_002",
    model_path: str = "yolov8n.pt",
    confidence_threshold: float = 0.35,
    device: str = "cpu",
    max_frames: Optional[int] = None,
    recursive: bool = True,
    extra_args: Optional[Iterable[str]] = None,
) -> List[Tuple[Path, Path, str]]:
    """
    Process every video in a folder and write per-camera JSONL outputs.

    Args:
        input_dir: Folder containing CCTV recordings.
        output_dir: Directory for per-camera event files.
        combined_output: Optional path for a single merged JSONL file.
        store_id: Store identifier applied to all events.
        model_path: YOLOv8 model path or name.
        confidence_threshold: Detection confidence threshold.
        device: Inference device.
        max_frames: Optional per-video frame cap.
        recursive: Search subfolders for videos.
        extra_args: Unused placeholder for future CLI passthrough.

    Returns:
        List of (video_path, output_path, camera_id) tuples processed successfully.
    """
    _ = extra_args
    videos = discover_videos(input_dir, recursive=recursive)
    if not videos:
        raise FileNotFoundError(f"No video files found under: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    if combined_output is not None:
        combined_output.parent.mkdir(parents=True, exist_ok=True)
        if combined_output.exists():
            combined_output.unlink()

    logger.info("found %s video(s) in %s", len(videos), input_dir)
    completed: List[Tuple[Path, Path, str]] = []

    for index, video_path in enumerate(videos, start=1):
        camera_id = derive_camera_id(video_path)
        per_camera_output = output_dir / f"{camera_id.lower()}_events.jsonl"

        logger.info(
            "[%s/%s] processing video=%s camera_id=%s output=%s",
            index,
            len(videos),
            video_path.name,
            camera_id,
            per_camera_output,
        )

        config = PipelineConfig(
            video_path=video_path,
            output_path=per_camera_output,
            model_path=model_path,
            camera_id=camera_id,
            store_id=store_id,
            confidence_threshold=confidence_threshold,
            device=device,
            max_frames=max_frames,
        )

        run_pipeline(config, clear_output=True)
        completed.append((video_path, per_camera_output, camera_id))

        if combined_output is not None:
            appended = append_file_contents(per_camera_output, combined_output)
            logger.info("appended %s events to %s", appended, combined_output)

    logger.info(
        "batch complete videos=%s output_dir=%s combined=%s",
        len(completed),
        output_dir,
        combined_output,
    )
    return completed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse batch CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Process all CCTV videos in a folder through the detection pipeline",
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Folder containing CCTV video files (searched recursively)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/cameras"),
        help="Directory for per-camera JSONL files (default: output/cameras)",
    )
    parser.add_argument(
        "--combined",
        type=Path,
        default=Path("output/events_all.jsonl"),
        help="Merged JSONL output path (default: output/events_all.jsonl)",
    )
    parser.add_argument(
        "--no-combined",
        action="store_true",
        help="Skip writing a merged JSONL file",
    )
    parser.add_argument("--store-id", default="STORE_BLR_002", help="Store identifier")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLOv8 model path or name")
    parser.add_argument("--conf", type=float, default=0.35, help="Detection confidence threshold")
    parser.add_argument("--device", default="cpu", help="Inference device (cpu, 0, 1, ...)")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional per-video frame limit")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top level of input_dir (default: recursive)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for batch processing."""
    args = parse_args(argv)
    configure_logging(verbose=args.verbose)

    if not args.input_dir.exists():
        logger.error("input directory not found: %s", args.input_dir)
        return 1

    combined = None if args.no_combined else args.combined

    try:
        results = run_batch(
            input_dir=args.input_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            combined_output=combined.resolve() if combined else None,
            store_id=args.store_id,
            model_path=args.model,
            confidence_threshold=args.conf,
            device=args.device,
            max_frames=args.max_frames,
            recursive=not args.no_recursive,
        )
    except Exception:
        logger.exception("batch processing failed")
        return 1

    print("\nBatch processing complete:")
    for video_path, output_path, camera_id in results:
        print(f"  {camera_id:12s}  {video_path.name}  ->  {output_path}")
    if combined:
        print(f"\nCombined output: {combined}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
