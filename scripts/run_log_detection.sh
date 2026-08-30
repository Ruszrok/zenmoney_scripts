#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$ROOT_DIR/scripts/detect_zenmoney_log.py" \
  "$ROOT_DIR/logs/eror_log.md" \
  "$ROOT_DIR/logs/error_log_1.md" \
  "$ROOT_DIR/logs/error_log_2.md" \
  "$ROOT_DIR/logs/error_log_3.md"
