# Shared by the local backend, runtime and MCP launchers.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
for env_file in "$ROOT_DIR/local.env" "$ROOT_DIR/.env.deepseek.local.env"; do
  if [[ -f "$env_file" ]]; then
    source "$env_file"
  fi
done
set +a
