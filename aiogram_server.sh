#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

env_get() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2-; }

TELEGRAM_API_ID="$(env_get API_ID)"
TELEGRAM_API_HASH="$(env_get API_HASH)"

if [[ -z "$TELEGRAM_API_ID" || -z "$TELEGRAM_API_HASH" ]]; then
  echo "TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы в .env" >&2
  exit 1
fi

docker run -d --name tg-bot-api --restart always \
  -e TELEGRAM_API_ID="$TELEGRAM_API_ID" \
  -e TELEGRAM_API_HASH="$TELEGRAM_API_HASH" \
  -e TELEGRAM_LOCAL=1 \
  -p 127.0.0.1:6767:8081 \
  aiogram/telegram-bot-api:latest