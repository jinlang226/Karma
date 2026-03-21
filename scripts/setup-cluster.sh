#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PROVIDER="${KARMA_CLUSTER_PROVIDER:-kind}"
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --provider)
      [ $# -ge 2 ] || {
        echo "setup-cluster: --provider requires a value" >&2
        exit 1
      }
      PROVIDER="$2"
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

case "$PROVIDER" in
  kind)
    if [ "${#ARGS[@]}" -eq 0 ]; then
      exec "$ROOT_DIR/scripts/setup-kind-cluster.sh"
    fi
    exec "$ROOT_DIR/scripts/setup-kind-cluster.sh" "${ARGS[@]}"
    ;;
  *)
    echo "setup-cluster: unsupported provider '$PROVIDER' (currently only 'kind' is implemented)" >&2
    exit 1
    ;;
esac
