#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/snapshot-repo-setup
# Strategy: native_shell
# Notes: chained workflows often inherit a TLS-enabled, password-protected
# cluster where the legacy vendored solver fails because it assumes:
# - plain HTTP
# - no auth
# - the `es-secure-settings` secret already exists
# This solver recreates the secret if needed, reloads secure settings over the
# live scheme, and makes the fixed snapshot name idempotent across repeated
# workflow stages.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
repo="${BENCH_PARAM_SNAPSHOT_REPO_NAME:-minio-repo}"
snapshot="${repo}-smoke-snapshot"
secure_settings_secret="es-secure-settings"
elastic_secret="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}}"
elastic_secret_key="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_KEY:-${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}}"
secret_manifest="${STATIC_SOLVER_REPO_ROOT}/cases/elasticsearch/snapshot-repo-setup/resource/es-secure-settings-secret.yaml"
service_host="${service}.${ns}.svc"
minio_endpoint="minio.${ns}.svc.cluster.local:9000"

SECRET_ACCESS_KEY=""
SECRET_SECRET_KEY=""
ELASTIC_PASSWORD=""
SCHEME=""
ES_LAST_BODY=""
ES_LAST_CODE=""

read_secret_jsonpath() {
  local secret_name="$1"
  local jsonpath="$2"
  kubectl -n "${ns}" get secret "${secret_name}" -o "jsonpath=${jsonpath}" 2>/dev/null | base64 -d 2>/dev/null || true
}

probe_scheme() {
  local scheme="$1"
  local output=""
  if ! output="$(
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -sS -k -o /dev/null -w '%{http_code}' --max-time 10 \
      "${scheme}://${service_host}:9200/" 2>/dev/null
  )"; then
    return 1
  fi

  [[ "${output}" =~ ^[0-9]{3}$ && "${output}" != "000" ]]
}

detect_scheme() {
  if probe_scheme "https"; then
    printf 'https\n'
    return 0
  fi
  if probe_scheme "http"; then
    printf 'http\n'
    return 0
  fi
  static_solver_fail "unable to detect live Elasticsearch HTTP scheme for ${service_host}"
}

es_request() {
  local method="$1"
  local path="$2"
  local payload="${3-}"
  local timeout="${4-60}"
  local -a cmd=(
    kubectl -n "${ns}" exec "${curl_pod}" --
    curl -sS -k --max-time "${timeout}" -X "${method}"
  )

  if [[ -n "${ELASTIC_PASSWORD}" ]]; then
    cmd+=(-u "elastic:${ELASTIC_PASSWORD}")
  fi

  if [[ -n "${payload}" ]]; then
    cmd+=(-H 'Content-Type: application/json' -d "${payload}")
  fi

  cmd+=(-w $'\n%{http_code}' "${SCHEME}://${service_host}:9200${path}")
  "${cmd[@]}"
}

run_es_request() {
  local method="$1"
  local path="$2"
  local payload="${3-}"
  local timeout="${4-60}"
  local response=""

  if ! response="$(es_request "${method}" "${path}" "${payload}" "${timeout}")"; then
    local rc=$?
    static_solver_fail "request ${method} ${path} failed with exit code ${rc}"
  fi

  ES_LAST_CODE="${response##*$'\n'}"
  ES_LAST_BODY="${response%$'\n'*}"
  if [[ "${ES_LAST_BODY}" == "${response}" ]]; then
    ES_LAST_BODY=""
  fi
}

require_http_success() {
  local context="$1"
  if [[ ! "${ES_LAST_CODE}" =~ ^2[0-9][0-9]$ ]]; then
    static_solver_fail "${context} returned HTTP ${ES_LAST_CODE}: ${ES_LAST_BODY}"
  fi
}

is_transient_repo_verification_error() {
  [[ "${ES_LAST_CODE}" =~ ^5[0-9][0-9]$ ]] || return 1
  [[ "${ES_LAST_BODY}" == *"repository_verification_exception"* ]] || return 1
  [[ "${ES_LAST_BODY}" == *"path  is not accessible on master node"* || \
     "${ES_LAST_BODY}" == *"sdk_client_exception"* || \
     "${ES_LAST_BODY}" == *"Unable to execute HTTP request"* ]]
}

list_es_pods() {
  python3 - "${ns}" <<'PY'
import json
import subprocess
import sys

ns = sys.argv[1]
result = subprocess.run(
    ["kubectl", "-n", ns, "get", "pods", "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
if result.returncode != 0:
    raise SystemExit(result.stderr.strip() or result.stdout.strip() or "failed to list pods")

items = json.loads(result.stdout).get("items", [])
for item in items:
    meta = item.get("metadata", {}) or {}
    if meta.get("deletionTimestamp"):
        continue
    name = meta.get("name") or ""
    if not name:
        continue
    containers = (item.get("spec", {}) or {}).get("containers") or []
    if any(
        container.get("name") == "elasticsearch"
        or "elasticsearch" in (container.get("image") or "")
        for container in containers
    ):
        print(name)
PY
}

kubectl -n "${ns}" apply -f "${secret_manifest}" >/dev/null
SECRET_ACCESS_KEY="$(read_secret_jsonpath "${secure_settings_secret}" '{.data.s3\.client\.default\.access_key}')"
SECRET_SECRET_KEY="$(read_secret_jsonpath "${secure_settings_secret}" '{.data.s3\.client\.default\.secret_key}')"
[[ -n "${SECRET_ACCESS_KEY}" ]] || static_solver_fail "missing s3.client.default.access_key in ${secure_settings_secret}"
[[ -n "${SECRET_SECRET_KEY}" ]] || static_solver_fail "missing s3.client.default.secret_key in ${secure_settings_secret}"

SCHEME="$(detect_scheme)"
ELASTIC_PASSWORD="$(read_secret_jsonpath "${elastic_secret}" "{.data.${elastic_secret_key}}")"

pods="$(list_es_pods)"
[[ -n "${pods}" ]] || static_solver_fail "no Elasticsearch pods found in ${ns}"

while IFS= read -r pod; do
  [[ -n "${pod}" ]] || continue
  printf '%s' "${SECRET_ACCESS_KEY}" | kubectl -n "${ns}" exec -i "${pod}" -- \
    /usr/share/elasticsearch/bin/elasticsearch-keystore add -x -f s3.client.default.access_key >/dev/null
  printf '%s' "${SECRET_SECRET_KEY}" | kubectl -n "${ns}" exec -i "${pod}" -- \
    /usr/share/elasticsearch/bin/elasticsearch-keystore add -x -f s3.client.default.secret_key >/dev/null
done <<< "${pods}"

run_es_request "POST" "/_nodes/reload_secure_settings" "{}" "120"
require_http_success "reload secure settings"

# Reusing the fixed snapshot name makes repeated workflow stages idempotent.
run_es_request "DELETE" "/_snapshot/${repo}/${snapshot}" "" "120"
if [[ "${ES_LAST_CODE}" != "200" && "${ES_LAST_CODE}" != "404" ]]; then
  static_solver_fail "delete prior snapshot returned HTTP ${ES_LAST_CODE}: ${ES_LAST_BODY}"
fi

repo_payload="$(python3 - "${minio_endpoint}" <<'PY'
import json
import sys

print(json.dumps({
    "type": "s3",
    "settings": {
        "bucket": "es-backups",
        "client": "default",
        "endpoint": sys.argv[1],
        "protocol": "http",
        "path_style_access": True,
    },
}))
PY
)"

run_es_request "PUT" "/_snapshot/${repo}" "${repo_payload}" "120"
if [[ ! "${ES_LAST_CODE}" =~ ^2[0-9][0-9]$ ]]; then
  repo_registered="false"
  for _ in $(seq 1 12); do
    if ! is_transient_repo_verification_error; then
      break
    fi
    sleep 10
    run_es_request "PUT" "/_snapshot/${repo}" "${repo_payload}" "120"
    if [[ "${ES_LAST_CODE}" =~ ^2[0-9][0-9]$ ]]; then
      repo_registered="true"
      break
    fi
  done
  if [[ "${repo_registered}" != "true" ]]; then
    require_http_success "register snapshot repository"
  fi
fi

run_es_request "PUT" "/_snapshot/${repo}/${snapshot}?wait_for_completion=true" "" "300"
require_http_success "create snapshot"

static_solver_write_submit "configured Elasticsearch snapshot repository and created snapshot"
