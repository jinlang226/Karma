#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/readonly-audit
# Strategy: native_shell
# Notes: Read-only collector. It captures current cluster state and writes a
# findings ConfigMap without changing any live Elasticsearch settings.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
ops_user="${BENCH_PARAM_OPS_USER:-ops-user}"
ops_password="${BENCH_PARAM_OPS_PASSWORD:-opspass}"
elastic_secret="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

elastic_password=""
if kubectl -n "${ns}" get secret "${elastic_secret}" >/dev/null 2>&1; then
  elastic_password="$(
    kubectl -n "${ns}" get secret "${elastic_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
  )"
fi

base_url=""
auth_desc="unauthenticated"
auth_args=()

try_endpoint() {
  local scheme="$1"
  shift
  kubectl -n "${ns}" exec "${curl_pod}" -- \
    curl -s -S -k --max-time 5 "$@" "${scheme}://${service}.${ns}.svc:9200/_cluster/health" 2>/dev/null || true
}

for scheme in https http; do
  output="$(try_endpoint "${scheme}")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="unauthenticated"
    auth_args=()
    break
  fi

  output="$(try_endpoint "${scheme}" -u "${ops_user}:${ops_password}")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="ops-user"
    auth_args=(-u "${ops_user}:${ops_password}")
    break
  fi

  if [[ -n "${elastic_password}" ]]; then
    output="$(try_endpoint "${scheme}" -u "elastic:${elastic_password}")"
    if [[ "${output}" == *'"status"'* ]]; then
      base_url="${scheme}://${service}.${ns}.svc:9200"
      auth_desc="elastic-secret"
      auth_args=(-u "elastic:${elastic_password}")
      break
    fi
  fi

  output="$(try_endpoint "${scheme}" -u "elastic:elasticpass")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="elastic-default"
    auth_args=(-u "elastic:elasticpass")
    break
  fi
done

audit_curl() {
  local path="$1"
  if [[ ${#auth_args[@]} -gt 0 ]]; then
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 10 "${auth_args[@]}" "${base_url}${path}" || true
  else
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 10 "${base_url}${path}" || true
  fi
}

findings_path="${tmp_dir}/findings.txt"
{
  echo "Elasticsearch read-only audit"
  echo "Namespace: ${ns}"
  echo "HTTP service: ${service}"
  echo "Detected access mode: ${auth_desc}"
  echo
  echo "Pods"
  kubectl -n "${ns}" get pods -o wide || true
  echo
  echo "StatefulSets"
  kubectl -n "${ns}" get statefulsets || true
  echo
  echo "Services"
  kubectl -n "${ns}" get svc || true
  echo
  echo "Secrets"
  kubectl -n "${ns}" get secret || true
  echo
  if [[ -n "${base_url}" ]]; then
    echo "Cluster health"
    audit_curl "/_cluster/health?pretty"
    echo
    echo "Cluster settings"
    audit_curl "/_cluster/settings?flat_settings=true&pretty"
    echo
    echo "Index settings"
    audit_curl "/_all/_settings?flat_settings=true&pretty"
  else
    echo "HTTP API was not reachable with unauthenticated, ops-user, or elastic credentials."
  fi
} > "${findings_path}"

kubectl -n "${ns}" create configmap config-audit \
  --from-file=findings.txt="${findings_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "captured Elasticsearch read-only audit findings"
