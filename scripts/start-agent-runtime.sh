#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/local-env.sh"

run_local_python uvicorn psych_agent_runtime.api:app --host 127.0.0.1 --port "${AGENT_RUNTIME_PORT:-8090}"
