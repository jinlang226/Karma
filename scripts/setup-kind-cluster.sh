#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

CLUSTER_NAME="${KIND_CLUSTER_NAME:-kind}"
BASE_IMAGE="${KIND_NODE_BASE_IMAGE:-kindest/node:v1.32.1}"
LOCAL_NODE_IMAGE="${KIND_NODE_IMAGE:-karma/kind-node:v1.32.1}"
CONFIG_PATH="${KIND_CLUSTER_CONFIG:-$ROOT_DIR/scripts/kind/cluster-4node.yaml}"
SMOKE_MODE="${KIND_SETUP_SMOKE:-rabbitmq}"
USE_OFFICIAL_NODE_IMAGE=0
RECREATE=0
KEEP_SMOKE_NAMESPACES="${KEEP_KIND_SMOKE_NAMESPACES:-0}"
SMOKE_NAMESPACES=()

usage() {
  cat <<'EOF'
Usage: scripts/setup-kind-cluster.sh [options]

Creates or reuses a local Kind cluster for KARMA and validates that the cluster
is usable for benchmark workloads.

Options:
  --cluster-name NAME          Kind cluster name (default: kind)
  --recreate                   Delete and recreate the cluster if it already exists
  --base-image IMAGE           Official/base Kind node image (default: kindest/node:v1.32.1)
  --node-image IMAGE           Local repo-owned node image tag (default: karma/kind-node:v1.32.1)
  --use-official-node-image    Skip local image build and create the cluster from the official image directly
  --smoke-mode MODE            Validation mode: rabbitmq, basic, none (default: rabbitmq)
  --keep-smoke-namespaces      Keep temporary smoke namespaces for debugging
  -h, --help                   Show this help
EOF
}

log() {
  printf '[kind-setup] %s\n' "$*"
}

die() {
  printf '[kind-setup] error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

cleanup_smoke_namespaces() {
  if [ "$KEEP_SMOKE_NAMESPACES" = "1" ]; then
    return
  fi
  if [ "${#SMOKE_NAMESPACES[@]}" -eq 0 ]; then
    return
  fi
  local ns
  for ns in "${SMOKE_NAMESPACES[@]}"; do
    kubectl delete namespace "$ns" --ignore-not-found >/dev/null 2>&1 || true
    kubectl wait --for=delete "namespace/$ns" --timeout=180s >/dev/null 2>&1 || true
  done
}

trap cleanup_smoke_namespaces EXIT

while [ $# -gt 0 ]; do
  case "$1" in
    --cluster-name)
      [ $# -ge 2 ] || die "--cluster-name requires a value"
      CLUSTER_NAME="$2"
      shift 2
      ;;
    --recreate)
      RECREATE=1
      shift
      ;;
    --base-image)
      [ $# -ge 2 ] || die "--base-image requires a value"
      BASE_IMAGE="$2"
      shift 2
      ;;
    --node-image)
      [ $# -ge 2 ] || die "--node-image requires a value"
      LOCAL_NODE_IMAGE="$2"
      shift 2
      ;;
    --use-official-node-image)
      USE_OFFICIAL_NODE_IMAGE=1
      shift
      ;;
    --smoke-mode)
      [ $# -ge 2 ] || die "--smoke-mode requires a value"
      SMOKE_MODE="$2"
      shift 2
      ;;
    --keep-smoke-namespaces)
      KEEP_SMOKE_NAMESPACES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$SMOKE_MODE" in
  rabbitmq|basic|none) ;;
  *) die "unsupported smoke mode: $SMOKE_MODE" ;;
esac

require_cmd docker
require_cmd kind
require_cmd kubectl
require_cmd envsubst

cluster_exists() {
  kind get clusters | grep -qx "$CLUSTER_NAME"
}

build_local_node_image() {
  if docker image inspect "$LOCAL_NODE_IMAGE" >/dev/null 2>&1; then
    log "Reusing local Kind node image $LOCAL_NODE_IMAGE"
    return
  fi

  log "Building repo-owned Kind node image $LOCAL_NODE_IMAGE from $BASE_IMAGE"
  docker build \
    --build-arg "BASE_IMAGE=$BASE_IMAGE" \
    -t "$LOCAL_NODE_IMAGE" \
    -f "$ROOT_DIR/scripts/kind-node/Dockerfile" \
    "$ROOT_DIR/scripts/kind-node"
}

wait_for_cluster_core() {
  log "Waiting for nodes to become Ready"
  kubectl wait --for=condition=Ready node --all --timeout=180s

  log "Waiting for core system workloads"
  kubectl -n kube-system rollout status deployment/coredns --timeout=180s
  kubectl -n kube-system rollout status daemonset/kube-proxy --timeout=180s
  kubectl -n kube-system rollout status daemonset/kindnet --timeout=180s
  kubectl -n local-path-storage rollout status deployment/local-path-provisioner --timeout=180s

  local node_count
  node_count="$(kubectl get nodes --no-headers | wc -l | tr -d ' ')"
  [ "$node_count" = "4" ] || die "expected 4 Kind nodes, found $node_count"

  for ns in default kube-node-lease kube-public kube-system local-path-storage; do
    kubectl get namespace "$ns" >/dev/null
  done
}

run_dns_smoke() {
  local ns="kind-dns-smoke"
  SMOKE_NAMESPACES+=("$ns")
  log "Running DNS smoke in namespace $ns"
  kubectl create namespace "$ns" >/dev/null 2>&1 || true
  kubectl -n "$ns" delete pod dns-smoke --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$ns" run dns-smoke \
    --image=busybox:1.36 \
    --restart=Never \
    --command -- \
    sh -c 'nslookup kubernetes.default.svc.cluster.local && nslookup kube-dns.kube-system.svc.cluster.local'
  if ! kubectl -n "$ns" wait --for=jsonpath='{.status.phase}'=Succeeded pod/dns-smoke --timeout=180s; then
    kubectl -n "$ns" logs dns-smoke || true
    kubectl -n "$ns" describe pod dns-smoke || true
    die "DNS smoke failed"
  fi
  kubectl -n "$ns" logs dns-smoke >/dev/null
}

render_apply() {
  local ns="$1"
  local prefix="$2"
  local file="$3"
  BENCH_NAMESPACE="$ns" BENCH_PARAM_CLUSTER_PREFIX="$prefix" envsubst < "$file" | kubectl -n "$ns" apply -f -
}

run_rabbitmq_smoke() {
  local ns="kind-rabbitmq-smoke"
  local prefix="setup-smoke"
  SMOKE_NAMESPACES+=("$ns")
  log "Running RabbitMQ bootstrap smoke in namespace $ns"
  kubectl create namespace "$ns" >/dev/null 2>&1 || true
  kubectl -n "$ns" create secret generic "${prefix}-erlang-cookie" \
    --from-literal=cookie=supersecretcookie \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$ns" create secret generic "${prefix}-admin" \
    --from-literal=username=admin \
    --from-literal=password=adminpass \
    --dry-run=client -o yaml | kubectl apply -f -

  render_apply "$ns" "$prefix" "$ROOT_DIR/resources/rabbitmq-experiments/manual_monitoring/resource/rbac.yaml"
  render_apply "$ns" "$prefix" "$ROOT_DIR/resources/rabbitmq-experiments/manual_monitoring/resource/configmap.yaml"
  render_apply "$ns" "$prefix" "$ROOT_DIR/resources/rabbitmq-experiments/manual_monitoring/resource/services.yaml"
  render_apply "$ns" "$prefix" "$ROOT_DIR/resources/rabbitmq-experiments/manual_monitoring/resource/statefulset.yaml"

  if ! kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=600s; then
    kubectl -n "$ns" get pods -o wide || true
    kubectl -n "$ns" logs "${prefix}-0" --all-containers --tail=200 || true
    kubectl -n "$ns" describe pod "${prefix}-0" || true
    die "RabbitMQ bootstrap smoke failed"
  fi

  [ "$(kubectl -n "$ns" get sts "$prefix" -o jsonpath='{.status.readyReplicas}')" = "3" ] \
    || die "RabbitMQ smoke finished without 3 ready replicas"
}

if [ "$USE_OFFICIAL_NODE_IMAGE" = "1" ]; then
  NODE_IMAGE="$BASE_IMAGE"
else
  build_local_node_image
  NODE_IMAGE="$LOCAL_NODE_IMAGE"
fi

if cluster_exists; then
  if [ "$RECREATE" = "1" ]; then
    log "Deleting existing Kind cluster $CLUSTER_NAME"
    kind delete cluster --name "$CLUSTER_NAME"
  else
    log "Reusing existing Kind cluster $CLUSTER_NAME"
  fi
fi

if ! cluster_exists; then
  log "Creating Kind cluster $CLUSTER_NAME with image $NODE_IMAGE"
  kind create cluster --name "$CLUSTER_NAME" --image "$NODE_IMAGE" --config "$CONFIG_PATH"
fi

log "Using kubectl context kind-$CLUSTER_NAME"
kubectl cluster-info >/dev/null
wait_for_cluster_core

case "$SMOKE_MODE" in
  rabbitmq)
    run_dns_smoke
    run_rabbitmq_smoke
    ;;
  basic)
    run_dns_smoke
    ;;
  none)
    ;;
esac

log "Cluster is ready"
kubectl get nodes
kubectl get namespaces
