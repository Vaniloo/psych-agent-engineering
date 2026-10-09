#!/usr/bin/env bash
set -euo pipefail
: "${ROUTER_CHECKPOINT:?Set ROUTER_CHECKPOINT to a trained checkpoint with deployment.json}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/agent-runtime"
exec uv run --extra routing uvicorn psych_agent_runtime.routing_service:app \
  --host "${ROUTER_HOST:-127.0.0.1}" --port "${ROUTER_PORT:-8091}"
