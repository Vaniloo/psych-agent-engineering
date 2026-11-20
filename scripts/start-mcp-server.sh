#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/local-env.sh"

run_local_python python -m psych_agent_runtime.mcp_server
