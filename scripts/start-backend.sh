#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/local-env.sh"

if [[ "${SKIP_INFRA_CHECK:-false}" != "true" ]]; then
  "$ROOT_DIR/scripts/start-local-infra.sh"
fi

cd "$ROOT_DIR/backend"
exec ./mvnw spring-boot:run
