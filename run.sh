#!/usr/bin/env bash
# Build the console and restart the QwenPaw service.
#
# The backend serves console/dist, so a backend-only restart leaves frontend
# changes invisible. Always build first.
#
# Usage: ./run.sh [--no-build] [--no-restart]

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE="qwenpaw"
HEALTH_URL="http://127.0.0.1:18888/api/healthz"

# vite exhausts the default ~2 GB heap on this codebase.
export NODE_OPTIONS="--max-old-space-size=4096"

do_build=1
do_restart=1
for arg in "$@"; do
  case "$arg" in
    --no-build) do_build=0 ;;
    --no-restart) do_restart=0 ;;
    -h | --help)
      sed -n '2,8p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "run.sh: unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '\n==> %s\n' "$1"; }

if [ "$do_build" -eq 1 ]; then
  log "Building console (heap ${NODE_OPTIONS##*=} MB)"
  # npm run build pipes through tsc and vite; without pipefail an OOM in vite
  # would be masked by a later stage exiting 0.
  (cd "$REPO_DIR/console" && npm run build)
  log "Built $(date -r "$REPO_DIR/console/dist/index.html" '+%F %T')"
fi

if [ "$do_restart" -eq 1 ]; then
  log "Restarting $SERVICE"
  sudo systemctl restart "$SERVICE"

  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      log "$SERVICE is healthy"
      exit 0
    fi
    sleep 1
  done

  log "$SERVICE did not become healthy within 30s"
  systemctl status "$SERVICE" --no-pager --lines=20 || true
  exit 1
fi
