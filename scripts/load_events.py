#!/usr/bin/env python3
"""Load JSONL pipeline output into the store intelligence API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

BATCH_SIZE = 500


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def ingest(base_url: str, events: List[Dict[str, Any]]) -> None:
    total_accepted = 0
    total_duplicates = 0
    total_rejected = 0

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        for offset in range(0, len(events), BATCH_SIZE):
            batch = events[offset : offset + BATCH_SIZE]
            response = client.post("/events/ingest", json={"events": batch})
            response.raise_for_status()
            body = response.json()
            total_accepted += body["accepted"]
            total_duplicates += body["duplicates"]
            total_rejected += body["rejected"]
            print(
                f"batch {offset // BATCH_SIZE + 1}: "
                f"accepted={body['accepted']} duplicates={body['duplicates']} "
                f"rejected={body['rejected']}"
            )

    print(
        f"done total_accepted={total_accepted} "
        f"duplicates={total_duplicates} rejected={total_rejected}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest JSONL events into the API")
    parser.add_argument("jsonl_path", type=Path, help="Path to events JSONL file")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="API base URL",
    )
    args = parser.parse_args()

    if not args.jsonl_path.exists():
        print(f"file not found: {args.jsonl_path}", file=sys.stderr)
        return 1

    events = load_jsonl(args.jsonl_path)
    print(f"loaded {len(events)} events from {args.jsonl_path}")
    ingest(args.url, events)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
