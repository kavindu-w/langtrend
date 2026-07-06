#!/usr/bin/env bash
# Fires a notification when a `make` target (wrapped by run_logged.sh) finishes.
# Configure via .env (see .env.example):
#   NOTIFY_METHOD=macos|webhook|none   (default: macos)
#   NOTIFY_WEBHOOK_URL=<url>           (required for webhook; must be a full URL
#                                        with scheme, e.g. https://ntfy.sh/<topic>
#                                        — a bare topic name silently fails, curl
#                                        can't resolve it as a host. ntfy.sh URLs
#                                        get ntfy's native plain-text+Title-header
#                                        format; anything else gets Slack/Discord-
#                                        style {"text": ...} JSON.)
#   NOTIFY_ON=always|failure           (default: always)
#
# Usage: scripts/notify.sh <name> <exit_code> <logfile>
set -uo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$root_dir/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$root_dir/.env"
  set +a
fi

name="$1"
status="$2"
logfile="$3"

method="${NOTIFY_METHOD:-macos}"
notify_on="${NOTIFY_ON:-always}"

if [ "$status" -eq 0 ]; then
  [ "$notify_on" = "failure" ] && exit 0
  title="make $name finished"
  body="Succeeded — $logfile"
else
  title="make $name FAILED"
  body="Exit code $status — $logfile"
fi

case "$method" in
  none)
    ;;
  webhook)
    if [ -n "${NOTIFY_WEBHOOK_URL:-}" ]; then
      case "$NOTIFY_WEBHOOK_URL" in
        *ntfy.sh*)
          # ntfy doesn't recognize a generic {"text": ...} body — it just
          # dumps the raw JSON as the message. Native format: plain-text
          # body is the message, Title header sets the title.
          curl -fsS -X POST -H "Title: ${title}" \
            -d "$body" \
            "$NOTIFY_WEBHOOK_URL" >/dev/null 2>&1 || true
          ;;
        *)
          curl -fsS -X POST -H 'Content-Type: application/json' \
            -d "{\"text\": \"${title} — ${body}\"}" \
            "$NOTIFY_WEBHOOK_URL" >/dev/null 2>&1 || true
          ;;
      esac
    fi
    ;;
  macos|*)
    if command -v osascript >/dev/null 2>&1; then
      osascript -e "display notification \"${body//\"/\\\"}\" with title \"${title//\"/\\\"}\"" >/dev/null 2>&1 || true
    fi
    ;;
esac
