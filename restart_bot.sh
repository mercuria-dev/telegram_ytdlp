#!/usr/bin/env bash
set -Eeuo pipefail

# Auto-restart runner for telegram_ytdlp bot.
# Creates per-run logs and records exit codes (incl. signal-derived codes like 137 = SIGKILL).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

mkdir -p logs logs/runs

RESTART_LOG="logs/restart.log"

activate_venv() {
  if [[ -f "venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "venv/bin/activate"
  fi
}

signal_name() {
  local sig="$1"
  case "$sig" in
    1) echo "SIGHUP";;
    2) echo "SIGINT";;
    3) echo "SIGQUIT";;
    6) echo "SIGABRT";;
    9) echo "SIGKILL";;
    11) echo "SIGSEGV";;
    13) echo "SIGPIPE";;
    14) echo "SIGALRM";;
    15) echo "SIGTERM";;
    *) echo "SIG${sig}";;
  esac
}

while true; do
  ts="$(date '+%Y-%m-%d_%H-%M-%S')"
  run_log="logs/runs/bot_${ts}.log"

  echo "[$(date -Is)] starting bot; log=${run_log}" | tee -a "$RESTART_LOG"

  activate_venv

  # Unbuffered output so logs contain last lines before crash.
  export PYTHONUNBUFFERED=1

  set +e
  python -u main.py >>"$run_log" 2>&1
  rc=$?
  set -e

  if (( rc >= 128 )); then
    sig=$((rc - 128))
    echo "[$(date -Is)] bot exited rc=${rc} (signal ${sig} / $(signal_name "$sig"))" | tee -a "$RESTART_LOG"
  else
    echo "[$(date -Is)] bot exited rc=${rc}" | tee -a "$RESTART_LOG"
  fi

  # Small backoff to avoid hot-looping.
  sleep 2

done
