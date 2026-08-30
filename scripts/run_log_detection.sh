#!/usr/bin/env bash
# Run the Apple-log analyser over the logs given as arguments, or over
# everything in logs/ when called with none. `logs/` is gitignored, so on a
# fresh clone you always pass paths explicitly:
#
#   scripts/run_log_detection.sh ~/Desktop/console-export.md --json
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DETECT="$ROOT_DIR/scripts/detect_zenmoney_log.py"

if [[ $# -gt 0 ]]; then
  exec python3 "$DETECT" "$@"
fi

shopt -s nullglob
default_logs=("$ROOT_DIR"/logs/*.md)
shopt -u nullglob

if [[ ${#default_logs[@]} -eq 0 ]]; then
  echo "usage: ${BASH_SOURCE[0]##*/} <log-file>... [--json]" >&2
  echo "no log files given and none found in $ROOT_DIR/logs/" >&2
  exit 1
fi

exec python3 "$DETECT" "${default_logs[@]}"
