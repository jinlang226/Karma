#!/usr/bin/env sh

if ! (return 0 2>/dev/null); then
  echo "Please source this script: source ./scripts/setup-proxy.sh"
  exit 1
fi

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
if [ ! -f "$ROOT/proxy.py" ]; then
  echo "proxy.py not found. Run this from the repo root."
  return 1
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl not found in PATH."
  return 1
fi

PROXY_LISTEN=${BENCHMARK_PROXY_LISTEN:-127.0.0.1:8081}
CONTROL_LISTEN=${BENCHMARK_PROXY_CONTROL_LISTEN:-127.0.0.1:8082}
CONTROL_URL=${BENCHMARK_PROXY_CONTROL_URL:-http://127.0.0.1:8082}

SOURCE_KUBECONFIG=${BENCHMARK_SOURCE_KUBECONFIG:-${KUBECONFIG%%:*}}
if [ -z "$SOURCE_KUBECONFIG" ]; then
  SOURCE_KUBECONFIG="$HOME/.kube/config"
fi
case "$SOURCE_KUBECONFIG" in
  */.benchmark/kubeconfig-proxy)
    if [ -z "${BENCHMARK_SOURCE_KUBECONFIG:-}" ]; then
      SOURCE_KUBECONFIG="$HOME/.kube/config"
    fi
    ;;
esac

if [ ! -f "$SOURCE_KUBECONFIG" ]; then
  echo "Source kubeconfig not found: $SOURCE_KUBECONFIG"
  return 1
fi

API_SERVER=$(KUBECONFIG="$SOURCE_KUBECONFIG" kubectl config view --minify \
  -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)
if [ -z "$API_SERVER" ]; then
  echo "Failed to resolve API server from kubeconfig: $SOURCE_KUBECONFIG"
  return 1
fi
API_SERVER=${API_SERVER#https://}
API_SERVER=${API_SERVER#http://}
API_SERVER=${API_SERVER%%/*}

check_proxy() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "$CONTROL_URL/status" >/dev/null 2>&1
    return $?
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$CONTROL_URL/status" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
try:
    urllib.request.urlopen(url, timeout=1).read()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
    return $?
  fi
  return 1
}

if check_proxy; then
  echo "Proxy already running at $CONTROL_URL"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found in PATH."
    return 1
  fi
  echo "Starting proxy..."
  python3 "$ROOT/proxy.py" --listen "$PROXY_LISTEN" --upstream "$API_SERVER" \
    --control-listen "$CONTROL_LISTEN" >/dev/null 2>&1 &
  sleep 1
fi

BENCH_DIR="$ROOT/.benchmark"
PROXY_KUBECONFIG="$BENCH_DIR/kubeconfig-proxy"
mkdir -p "$BENCH_DIR"

KUBECONFIG="$SOURCE_KUBECONFIG" kubectl config view --raw --minify --flatten 2>/dev/null \
  | sed -E "s#^(\\s*server:) .*#\\1 https://$PROXY_LISTEN#" > "$PROXY_KUBECONFIG"

BENCH_BIN="$BENCH_DIR/bin"
mkdir -p "$BENCH_BIN"

REAL_KUBECTL=""
if command -v which >/dev/null 2>&1; then
  REAL_KUBECTL=$(which -a kubectl 2>/dev/null | awk -v bench="$BENCH_BIN/kubectl" '$0!=bench{print; exit}')
fi
if [ -z "$REAL_KUBECTL" ]; then
  REAL_KUBECTL=$(command -v kubectl)
fi
if [ -z "$REAL_KUBECTL" ]; then
  echo "Unable to resolve real kubectl binary."
  return 1
fi

cat > "$BENCH_BIN/kubectl" <<EOF
#!/usr/bin/env sh
ROOT_DIR=\$(CDPATH= cd -- "\$(dirname "\$0")/../.." && pwd)
REAL_KUBECTL=\${BENCHMARK_REAL_KUBECTL:-$REAL_KUBECTL}
TRACE_FILE=\${BENCHMARK_ACTION_TRACE_LOG:-}
if [ -z "\$TRACE_FILE" ]; then
  ACTIVE_FILE="\$ROOT_DIR/.benchmark/active_trace_path"
  if [ -f "\$ACTIVE_FILE" ]; then
    TRACE_FILE=\$(cat "\$ACTIVE_FILE")
  fi
fi
if [ -n "\$TRACE_FILE" ]; then
  TS=\$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  if command -v python3 >/dev/null 2>&1; then
    python3 - "\$TRACE_FILE" "\$TS" "\$@" <<'PY'
import json
import sys

trace, ts, *args = sys.argv[1:]
record = {"ts": ts, "command": ["kubectl"] + args}
with open(trace, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\\n")
PY
  else
    printf '%s\n' "{\"ts\":\"\$TS\",\"command\":\"kubectl \$*\"}" >> "\$TRACE_FILE"
  fi
fi
exec "\$REAL_KUBECTL" "\$@"
EOF

chmod +x "$BENCH_BIN/kubectl"

export BENCHMARK_REAL_KUBECTL="$REAL_KUBECTL"
case ":$PATH:" in
  *":$BENCH_BIN:"*) ;;
  *) export PATH="$BENCH_BIN:$PATH" ;;
esac

export KUBECONFIG="$PROXY_KUBECONFIG"
export BENCHMARK_PROXY_CONTROL_URL="$CONTROL_URL"

echo "Proxy kubeconfig: $PROXY_KUBECONFIG"
echo "KUBECONFIG set for this shell."
