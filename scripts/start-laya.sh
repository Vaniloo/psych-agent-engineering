#!/usr/bin/env bash
set -euo pipefail
export DECISION_PROFILE="${1:-router}"
case "$DECISION_PROFILE" in
  router) decision_port=8091 ;;
  memory) decision_port=8092 ;;
  executor) decision_port=8093 ;;
  *) echo 'Use router, executor or memory' >&2; exit 2 ;;
esac
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/agent-runtime"
# Checkpoint is optional while building the architecture; no GPU imports then.
if [[ -n "${DECISION_CHECKPOINT:-}" ]]; then
  exec uv run --extra routing uvicorn psych_agent_runtime.routing_service:app --host "${ROUTER_HOST:-127.0.0.1}" --port "${ROUTER_PORT:-$decision_port}"
else
  exec uv run uvicorn psych_agent_runtime.routing_service:app --host "${ROUTER_HOST:-127.0.0.1}" --port "${ROUTER_PORT:-$decision_port}"
fi
