# Shared by the local backend, runtime and MCP launchers.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
for env_file in "$ROOT_DIR/local.env" "$ROOT_DIR/.env.deepseek.local.env"; do
  if [[ -f "$env_file" ]]; then
    source "$env_file"
  fi
done
set +a

run_local_python() {
  export NO_PROXY="localhost,127.0.0.1,::1"
  export no_proxy="$NO_PROXY"
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/psych-agent-uv-cache}"
  cd "$ROOT_DIR/agent-runtime"
  exec uv run --extra dev "$@"
}
