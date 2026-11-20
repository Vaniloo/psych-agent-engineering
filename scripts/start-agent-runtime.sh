#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/local-env.sh"

export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="$NO_PROXY"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

cd "$ROOT_DIR/agent-runtime"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/psych-agent-uv-cache}"
exec uv run --extra dev uvicorn psych_agent_runtime.api:app --host 127.0.0.1 --port "${AGENT_RUNTIME_PORT:-8090}"
