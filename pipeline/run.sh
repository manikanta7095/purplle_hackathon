#!/usr/bin/env bash
#
# Run the retail store intelligence detection pipeline.
#
# Usage:
#   ./pipeline/run.sh /path/to/video.mp4
#   ./pipeline/run.sh /path/to/video.mp4 --device 0 --verbose
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_PATH="${PROJECT_ROOT}/output/events.jsonl"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <video_path> [extra detect.py args...]" >&2
  exit 1
fi

VIDEO_PATH="$1"
shift

if [[ ! -f "${VIDEO_PATH}" ]]; then
  echo "Error: video file not found: ${VIDEO_PATH}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

mkdir -p "${PROJECT_ROOT}/output"

echo "Starting store intelligence pipeline"
echo "  video:  ${VIDEO_PATH}"
echo "  output: ${OUTPUT_PATH}"

python -m pipeline.detect "${VIDEO_PATH}" \
  --output "${OUTPUT_PATH}" \
  "$@"

echo "Events written to ${OUTPUT_PATH}"
