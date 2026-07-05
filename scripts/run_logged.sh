#!/usr/bin/env bash
# Runs a command, tee-ing its output to logs/<name>_<timestamp>.log, then
# fires a notification (see scripts/notify.sh) with the outcome. Used by the
# Makefile to wrap pipeline steps so long-running/background runs (e.g.
# `make judge` hitting a daily quota) leave an inspectable log and a ping.
#
# Usage: scripts/run_logged.sh <name> <command...>
set -uo pipefail

name="$1"
shift

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_dir="$root_dir/logs"
mkdir -p "$log_dir"

logfile="$log_dir/${name}_$(date +%Y%m%d_%H%M%S).log"

{
  echo "==> $name started at $(date)"
  echo "==> command: $*"
} | tee "$logfile"

"$@" 2>&1 | tee -a "$logfile"
status="${PIPESTATUS[0]}"

echo "==> $name finished with exit code $status at $(date)" | tee -a "$logfile"

"$root_dir/scripts/notify.sh" "$name" "$status" "$logfile" || true

exit "$status"
