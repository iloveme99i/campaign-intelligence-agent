#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${CAMPAIGN_RUNTIME_DIR:-$PROJECT_DIR/.merchant-runtime/data}"
PORT="${1:-8101}"
HOST="${CAMPAIGN_HOST:-127.0.0.1}"

mkdir -p "$RUNTIME_DIR"
export DATABASE_URL="sqlite+aiosqlite:///$RUNTIME_DIR/agent.db"
export DATAHUB_TELEMETRY_ENABLED=false
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8

# Do not inherit a system HTTP/HTTPS proxy. Teams that require a proxy can pass
# CAMPAIGN_PROXY_URL explicitly, for example socks5h://127.0.0.1:7898.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
if [[ -n "${CAMPAIGN_PROXY_URL:-}" ]]; then
  export ALL_PROXY="$CAMPAIGN_PROXY_URL"
  export all_proxy="$ALL_PROXY"
else
  unset ALL_PROXY all_proxy
fi
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"

"$PROJECT_DIR/.venv/bin/campaign-intelligence" bootstrap

exec "$PROJECT_DIR/.venv/bin/uvicorn" analytics_agent.main:app \
  --app-dir "$PROJECT_DIR/backend/src" \
  --host "$HOST" \
  --port "$PORT"
