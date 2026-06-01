#!/usr/bin/env bash
#
# Process all CCTV videos in a folder through the store intelligence pipeline.
#
# Usage:
#   ./pipeline/run_batch.sh "/path/to/CCTV Footage-20260529T160731Z-3-00144614ea"
#   ./pipeline/run_batch.sh "/path/to/folder" --device 0 --verbose
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <input_dir> [extra batch.py args...]" >&2
  exit 1
fi

INPUT_DIR="$1"
shift

if [[ ! -d "${INPUT_DIR}" ]]; then
  echo "Error: input directory not found: ${INPUT_DIR}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

if [[ -d ".venv" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
elif [[ -d "../venv/bin" ]]; then
  # shellcheck disable=SC1091
  source "../venv/bin/activate"
fi

export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

echo "Starting batch store intelligence pipeline"
echo "  input:  ${INPUT_DIR}"
echo "  output: ${PROJECT_ROOT}/output/cameras/"
echo "  merged: ${PROJECT_ROOT}/output/events_all.jsonl"
echo

python -m pipeline.batch "${INPUT_DIR}" "$@"

echo
echo "Done. Per-camera files in output/cameras/; merged file at output/events_all.jsonl"
