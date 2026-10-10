#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -f "$ROOT_DIR/local.env" ]]; then
  set -a
  source "$ROOT_DIR/local.env"
  set +a
fi

if [[ -f "$ROOT_DIR/.env.deepseek.local.env" ]]; then
  set -a
  source "$ROOT_DIR/.env.deepseek.local.env"
  set +a
fi

export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

cd "$ROOT_DIR/agent-runtime"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/psych-agent-uv-cache}"
uv sync --extra dev
exec uv run python -m psych_agent_runtime.mcp_server
