#!/usr/bin/env bash
# Build the console and restart the QwenPaw service.
#
# The backend serves console/dist, so a backend-only restart leaves frontend
# changes invisible. Always build first.
#
# Prints how long each stage took, plus a summary table at the end.
#
# Usage: ./run.sh [--no-build] [--no-restart]

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONSOLE_DIR="$REPO_DIR/console"
BIN_DIR="$CONSOLE_DIR/node_modules/.bin"
SERVICE="qwenpaw"
HEALTH_URL="http://127.0.0.1:18888/api/healthz"
# The port only opens once every agent's sessions are synced; ~70 s here.
HEALTH_WAIT_S=120

# vite exhausts the default ~2 GB heap on this codebase.
export NODE_OPTIONS="--max-old-space-size=4096"

do_build=1
do_restart=1
for arg in "$@"; do
  case "$arg" in
    --no-build) do_build=0 ;;
    --no-restart) do_restart=0 ;;
    -h | --help)
      sed -n '2,9p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "run.sh: unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

log() { printf '\n==> %s\n' "$1"; }

now_ms() { date +%s%3N; }

# 1234 -> "1.23s"; 215000 -> "3m 35s"; 730 -> "730ms"
fmt_dur() {
  local ms=$1
  if [ "$ms" -lt 1000 ]; then
    printf '%dms' "$ms"
  elif [ "$ms" -lt 60000 ]; then
    printf '%d.%02ds' "$((ms / 1000))" "$(((ms % 1000) / 10))"
  else
    printf '%dm %02ds' "$((ms / 60000))" "$(((ms % 60000) / 1000))"
  fi
}

STAGE_NAMES=()
STAGE_MS=()
RUN_START=$(now_ms)

# Runs a stage, records its duration, and echoes it immediately so a slow
# stage is visible while the rest of the run is still going.
stage() {
  local name="$1"
  shift
  log "$name"
  local t0 t1 ms rc=0
  t0=$(now_ms)
  # `|| rc=$?` keeps set -e from killing us here, so a stage that dies still
  # gets its duration recorded before we propagate the failure.
  "$@" || rc=$?
  t1=$(now_ms)
  ms=$((t1 - t0))
  if [ "$rc" -ne 0 ]; then
    name="$name (FAILED: exit $rc)"
  fi
  STAGE_NAMES+=("$name")
  STAGE_MS+=("$ms")
  printf '    [%s] %s\n' "$(fmt_dur "$ms")" "$name"
  return "$rc"
}

# Printed via trap so a failed run still reports where the time went.
summary() {
  [ "${#STAGE_NAMES[@]}" -gt 0 ] || return 0
  local i width=0
  for i in "${!STAGE_NAMES[@]}"; do
    [ "${#STAGE_NAMES[$i]}" -gt "$width" ] && width=${#STAGE_NAMES[$i]}
  done
  printf '\n==> Summary\n'
  for i in "${!STAGE_NAMES[@]}"; do
    printf '    %-*s  %10s\n' "$width" "${STAGE_NAMES[$i]}" "$(fmt_dur "${STAGE_MS[$i]}")"
  done
  printf '    %-*s  %10s\n' "$width" "$(printf '%.0s-' $(seq 1 "$width"))" '----------'
  printf '    %-*s  %10s\n' "$width" "total" "$(fmt_dur "$(($(now_ms) - RUN_START))")"
}
trap summary EXIT

# Mirrors console/package.json "build", split into separate stages so each one
# is timed on its own. `precompress` and `verify:initial-bundle` are left out on
# purpose: precompress only writes the .br/.gz copies that hub mode serves, and
# the bundle guard measures its Brotli budget on those copies (without them it
# compares the raw size against that budget and always fails).
# npm records the installed tree in node_modules/.package-lock.json; when the
# lockfile or package.json is newer (pull, rebase), dependencies are stale.
deps_stale() {
  local installed="$CONSOLE_DIR/node_modules/.package-lock.json"
  [ ! -f "$installed" ] ||
    [ "$CONSOLE_DIR/package-lock.json" -nt "$installed" ] ||
    [ "$CONSOLE_DIR/package.json" -nt "$installed" ]
}
install_deps() { (cd "$CONSOLE_DIR" && npm install --no-audit --no-fund); }
typecheck() { (cd "$CONSOLE_DIR" && "$BIN_DIR/tsc" -b); }
bundle() { (cd "$CONSOLE_DIR" && "$BIN_DIR/vite" build); }
verify_monaco() { (cd "$CONSOLE_DIR" && node scripts/verify-monaco-css.mjs); }

# A branch switch or rebase can change pyproject.toml/uv.lock while the running
# service still has the old code loaded; the next restart then dies on a missing
# module. The stamp records the last successful install.
VENV_PY="$REPO_DIR/.venv/bin/python"
PY_DEPS_STAMP="$REPO_DIR/.venv/.run-sh-deps-stamp"
# cron's PATH lacks ~/.local/bin, where uv lives.
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
py_deps_stale() {
  [ ! -f "$PY_DEPS_STAMP" ] ||
    [ "$REPO_DIR/pyproject.toml" -nt "$PY_DEPS_STAMP" ] ||
    [ "$REPO_DIR/uv.lock" -nt "$PY_DEPS_STAMP" ]
}
# Additive on purpose (not `uv sync`): never removes packages from the venv.
install_py_deps() {
  (cd "$REPO_DIR" && "$UV" pip install --python "$VENV_PY" -e ".[dev,test,full]") &&
    touch "$PY_DEPS_STAMP"
}

restart_service() { sudo systemctl restart "$SERVICE"; }

wait_healthy() {
  for _ in $(seq 1 "$HEALTH_WAIT_S"); do
    if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "$SERVICE did not become healthy within ${HEALTH_WAIT_S}s" >&2
  systemctl status "$SERVICE" --no-pager --lines=20 || true
  return 1
}

if [ "$do_build" -eq 1 ]; then
  if deps_stale; then
    stage "npm install" install_deps
  else
    log "npm install skipped (node_modules up to date)"
  fi
  stage "tsc -b" typecheck
  stage "vite build" bundle
  stage "verify monaco css" verify_monaco
  log "Built $(date -r "$CONSOLE_DIR/dist/index.html" '+%F %T')"
fi

if [ "$do_restart" -eq 1 ]; then
  if py_deps_stale; then
    stage "uv pip install" install_py_deps
  else
    log "uv pip install skipped (venv up to date)"
  fi
  stage "systemctl restart $SERVICE" restart_service
  stage "health check" wait_healthy
  log "$SERVICE is healthy"
fi
