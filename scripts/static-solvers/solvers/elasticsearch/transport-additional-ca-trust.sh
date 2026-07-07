#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/transport-additional-ca-trust
# Strategy: native_shell
# Notes: the archived solver assumes the standalone transport-CA secrets always
# exist. In chained workflows the inherited ES cluster can be healthy while only
# the oracle-only bundle ConfigMap is missing or empty, so repair that state
# additively instead of overwriting it from absent standalone secrets.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
bundle="${BENCH_PARAM_TRANSPORT_BUNDLE_CONFIGMAP:-es-transport-ca-bundle}"
ca1_secret="${BENCH_PARAM_CA1_SECRET_NAME:-es-transport-ca1}"
ca2_secret="${BENCH_PARAM_CA2_SECRET_NAME:-es-transport-ca2}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
expected_nodes="${BENCH_PARAM_EXPECTED_NODE_COUNT:-${BENCH_PARAM_EXPECTED_NODES:-3}}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

bundle_path="${tmp_dir}/bundle.crt"
oracle_output="${tmp_dir}/oracle.log"
sts_json="${tmp_dir}/statefulset.json"

[[ "${expected_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "expected node count must be numeric"
(( expected_nodes > 0 )) || static_solver_fail "expected node count must be positive"

oracle_passes() {
  python3 cases/elasticsearch/transport-additional-ca-trust/oracle/oracle.py >"${oracle_output}" 2>&1
}

wait_for_oracle() {
  local deadline=$((SECONDS + 180))
  while (( SECONDS < deadline )); do
    if oracle_passes; then
      return 0
    fi
    sleep 5
  done

  if [[ -f "${oracle_output}" ]]; then
    cat "${oracle_output}" >&2
  fi
  static_solver_fail "transport CA trust oracle still failing after bundle repair"
}

ensure_http_service_selector() {
  kubectl -n "${ns}" apply -f - <<YAML >/dev/null
apiVersion: v1
kind: Service
metadata:
  name: ${service}
  namespace: ${ns}
spec:
  selector:
    app: ${prefix}
  ports:
    - name: http
      port: 9200
      targetPort: 9200
YAML
}

ensure_curl_pod() {
  if ! kubectl -n "${ns}" get pod "${curl_pod}" >/dev/null 2>&1; then
    kubectl -n "${ns}" apply -f \
      "cases/elasticsearch/transport-additional-ca-trust/resource/curl-test.yaml" >/dev/null
  fi
  kubectl -n "${ns}" wait --for=condition=ready "pod/${curl_pod}" --timeout=300s >/dev/null
}

ensure_openssl_toolbox() {
  if ! kubectl -n "${ns}" get pod openssl-toolbox >/dev/null 2>&1; then
    kubectl -n "${ns}" apply -f \
      "cases/elasticsearch/transport-additional-ca-trust/resource/openssl-toolbox.yaml" >/dev/null
  fi
  kubectl -n "${ns}" wait --for=condition=ready pod/openssl-toolbox --timeout=300s >/dev/null
}

statefulset_mounts_bundle() {
  kubectl -n "${ns}" get statefulset "${prefix}" -o json > "${sts_json}"
  python3 - "${sts_json}" "${bundle}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
bundle = sys.argv[2]
volumes = (((payload.get("spec") or {}).get("template") or {}).get("spec") or {}).get("volumes") or []
print(
    "true"
    if any((volume.get("configMap") or {}).get("name") == bundle for volume in volumes)
    else "false"
)
PY
}

configmap_cert_count() {
  kubectl -n "${ns}" get configmap "${bundle}" -o "jsonpath={.data.ca\\.crt}" 2>/dev/null | \
    python3 -c 'import sys; print(sys.stdin.read().count("BEGIN CERTIFICATE"))'
}

build_bundle_from_secrets() {
  local ca1_path="${tmp_dir}/ca1.crt"
  local ca2_path="${tmp_dir}/ca2.crt"

  kubectl -n "${ns}" get secret "${ca1_secret}" -o "jsonpath={.data.ca\\.crt}" | \
    base64 -d > "${ca1_path}"
  kubectl -n "${ns}" get secret "${ca2_secret}" -o "jsonpath={.data.ca\\.crt}" | \
    base64 -d > "${ca2_path}"

  [[ -s "${ca1_path}" && -s "${ca2_path}" ]] || return 1
  cat "${ca1_path}" "${ca2_path}" > "${bundle_path}"
  [[ "$(python3 - "${bundle_path}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

print(Path(sys.argv[1]).read_text().count("BEGIN CERTIFICATE"))
PY
)" -ge 2 ]]
}

build_fixture_bundle() {
  ensure_openssl_toolbox
  kubectl -n "${ns}" exec openssl-toolbox -- /bin/sh -c '
set -e
rm -rf /tmp/static-solver-transport-ca
mkdir -p /tmp/static-solver-transport-ca
openssl genrsa -out /tmp/static-solver-transport-ca/ca1.key 2048 >/dev/null 2>&1
openssl req -x509 -new -nodes -key /tmp/static-solver-transport-ca/ca1.key -sha256 -days 3650 \
  -subj "/CN=es-transport-ca-1" -out /tmp/static-solver-transport-ca/ca1.crt >/dev/null 2>&1
openssl genrsa -out /tmp/static-solver-transport-ca/ca2.key 2048 >/dev/null 2>&1
openssl req -x509 -new -nodes -key /tmp/static-solver-transport-ca/ca2.key -sha256 -days 3650 \
  -subj "/CN=es-transport-ca-2" -out /tmp/static-solver-transport-ca/ca2.crt >/dev/null 2>&1
cat /tmp/static-solver-transport-ca/ca1.crt /tmp/static-solver-transport-ca/ca2.crt > \
  /tmp/static-solver-transport-ca/bundle.crt
'
  kubectl -n "${ns}" exec openssl-toolbox -- cat /tmp/static-solver-transport-ca/bundle.crt > "${bundle_path}"
}

apply_bundle_configmap() {
  kubectl -n "${ns}" create configmap "${bundle}" \
    --from-file=ca.crt="${bundle_path}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
}

restart_transport_cluster() {
  static_solver_log "restarting Elasticsearch pods so the repaired transport CA bundle is remounted"
  kubectl -n "${ns}" rollout restart "statefulset/${prefix}" >/dev/null
  kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s >/dev/null
  kubectl -n "${ns}" wait --for=condition=ready "pod/${prefix}-0" --timeout=300s >/dev/null
  kubectl -n "${ns}" wait --for=condition=ready "pod/${prefix}-1" --timeout=300s >/dev/null
  kubectl -n "${ns}" wait --for=condition=ready "pod/${prefix}-2" --timeout=300s >/dev/null
}

ensure_http_service_selector
ensure_curl_pod

if oracle_passes; then
  static_solver_write_submit "verified existing transport trust bundle"
  exit 0
fi

bundle_mounted="$(statefulset_mounts_bundle)"
bundle_source=""

if build_bundle_from_secrets; then
  bundle_source="secret-backed"
elif [[ "${bundle_mounted}" == "false" ]]; then
  build_fixture_bundle
  bundle_source="fixture"
else
  static_solver_fail \
    "transport CA bundle is mounted by statefulset/${prefix}, but ${ca1_secret}/${ca2_secret} are unavailable"
fi

apply_bundle_configmap

if [[ "$(configmap_cert_count)" -lt 2 ]]; then
  static_solver_fail "transport CA bundle repair wrote fewer than two certificates"
fi

if [[ "${bundle_mounted}" == "true" && "${bundle_source}" == "secret-backed" ]]; then
  restart_transport_cluster
fi

wait_for_oracle
static_solver_write_submit "expanded transport trust bundle"
