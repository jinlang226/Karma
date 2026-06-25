#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/rotate-elastic-password
# Strategy: native_shell
# Notes: The standalone case assumes plain HTTP, but chained workflows often
# inherit a TLS-enabled es-http service from deploy-core-cluster /
# rotate-http-certs. Detect the live scheme, rotate the password over that
# endpoint, and rewrite auth-checker so its readiness probe matches the live
# cluster.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
current_secret="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-elastic-password}"
next_secret="${BENCH_PARAM_NEXT_PASSWORD_SECRET_NAME:-elastic-password-next}"
checker="${BENCH_PARAM_AUTH_CHECKER_DEPLOYMENT_NAME:-auth-checker}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
checker_image="$(
  kubectl -n "${ns}" get deployment "${checker}" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true
)"
checker_image="${checker_image:-curlimages/curl:8.5.0}"

service_host="${service}.${ns}.svc"

read_secret_password() {
  local secret_name="$1"
  kubectl -n "${ns}" get secret "${secret_name}" -o jsonpath='{.data.password}' | base64 -d
}

probe_scheme() {
  local scheme="$1"
  local -a cmd=(
    kubectl -n "${ns}" exec "${curl_pod}" --
    curl -sS -o /dev/null -w '%{http_code}' --max-time 10
  )
  if [[ "${scheme}" == "https" ]]; then
    cmd+=(-k)
  fi
  cmd+=("${scheme}://${service_host}:9200/")

  local output
  if ! output="$("${cmd[@]}" 2>/dev/null)"; then
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

curl_with_scheme() {
  local scheme="$1"
  shift
  local -a cmd=(kubectl -n "${ns}" exec "${curl_pod}" -- curl -sS --max-time 20)
  if [[ "${scheme}" == "https" ]]; then
    cmd+=(-k)
  fi
  cmd+=("$@")
  "${cmd[@]}"
}

verify_auth_code() {
  local scheme="$1"
  local password="$2"
  local expected="$3"
  local output
  if ! output="$(
    curl_with_scheme "${scheme}" \
      -u "elastic:${password}" \
      -w $'\n%{http_code}' \
      "${scheme}://${service_host}:9200/_security/_authenticate"
  )"; then
    static_solver_fail "authentication probe failed for ${service_host}"
  fi

  local code="${output##*$'\n'}"
  if [[ "${code}" != "${expected}" ]]; then
    static_solver_fail "expected auth status ${expected}, got ${code}"
  fi
}

apply_auth_checker_manifest() {
  local scheme="$1"
  local manifest="${STATIC_SOLVER_STAGE_DIR}/auth-checker.yaml"
  local annotation
  annotation="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local url="${scheme}://${service_host}:9200/_security/_authenticate"
  local flags=""

  if [[ "${scheme}" == "https" ]]; then
    flags="-k"
  fi

  cat > "${manifest}" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${checker}
  namespace: ${ns}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${checker}
  template:
    metadata:
      labels:
        app: ${checker}
      annotations:
        static-solver/restarted-at: "${annotation}"
    spec:
      containers:
        - name: checker
          image: ${checker_image}
          env:
            - name: ELASTIC_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ${current_secret}
                  key: password
          command:
            - /bin/sh
            - -c
            - |
              sleep infinity
          readinessProbe:
            exec:
              command:
                - /bin/sh
                - -c
                - |
                  curl -sS --max-time 10 ${flags} -u elastic:\${ELASTIC_PASSWORD} ${url} | grep -q '"username":"elastic"'
            initialDelaySeconds: 10
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 6
EOF

  kubectl -n "${ns}" apply -f "${manifest}"
}

scheme="$(detect_scheme)"
old_password="$(read_secret_password "${current_secret}")"
new_password="$(read_secret_password "${next_secret}")"
payload="$(python3 - "${new_password}" <<'PY'
import json
import sys

print(json.dumps({"password": sys.argv[1]}))
PY
)"

rotate_output="$(
  curl_with_scheme "${scheme}" \
    -u "elastic:${old_password}" \
    -XPOST \
    -H 'Content-Type: application/json' \
    -d "${payload}" \
    -w $'\n%{http_code}' \
    "${scheme}://${service_host}:9200/_security/user/elastic/_password"
)"
rotate_code="${rotate_output##*$'\n'}"
if [[ "${rotate_code}" != "200" ]]; then
  static_solver_fail "password rotation returned HTTP ${rotate_code}"
fi

kubectl -n "${ns}" create secret generic "${current_secret}" \
  --from-literal=password="${new_password}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

apply_auth_checker_manifest "${scheme}"
kubectl -n "${ns}" rollout status "deployment/${checker}" --timeout=600s

verify_auth_code "${scheme}" "${new_password}" "200"
verify_auth_code "${scheme}" "${old_password}" "401"

static_solver_write_submit "rotated elastic password and refreshed auth-checker"
