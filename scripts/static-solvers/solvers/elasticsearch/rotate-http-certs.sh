#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/rotate-http-certs
# Strategy: direct_shell
# Notes: Rotate the HTTP CA/leaf material and recreate the curl helper pod
# without relying on envsubst being installed in the runtime.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
ca_cm="${BENCH_PARAM_HTTP_CA_CONFIGMAP_NAME:-es-http-ca}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"
expected_nodes="${BENCH_PARAM_EXPECTED_NODE_COUNT:-${BENCH_PARAM_EXPECTED_NODES:-3}}"

[[ "${expected_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "expected node count must be numeric"
(( expected_nodes > 0 )) || static_solver_fail "expected node count must be positive"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

read_secret_password() {
  kubectl -n "${ns}" get secret "${password_secret}" -o "jsonpath={.data.${password_key}}" 2>/dev/null | base64 -d 2>/dev/null || true
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

wait_for_expected_elasticsearch_pods() {
  python3 - "${ns}" "${prefix}" "${expected_nodes}" <<'PY'
import json
import subprocess
import sys
import time

ns, prefix, expected_nodes = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.monotonic() + 900
last_error = f"timed out waiting for {expected_nodes} ready pods for {prefix} in {ns}"

while time.monotonic() < deadline:
    result = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={prefix}", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        last_error = result.stderr.strip() or "failed to read Elasticsearch pods"
        time.sleep(5)
        continue

    payload = json.loads(result.stdout)
    items = payload.get("items", [])
    if len(items) != expected_nodes:
        last_error = f"expected {expected_nodes} pods, got {len(items)}"
        time.sleep(5)
        continue

    not_ready = []
    for item in items:
        name = item.get("metadata", {}).get("name", "unknown")
        conditions = item.get("status", {}).get("conditions", []) or []
        ready = any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions)
        if not ready:
            not_ready.append(name)

    if not not_ready:
        raise SystemExit(0)

    last_error = f"pods not ready: {', '.join(not_ready)}"
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

ensure_expected_topology() {
  static_solver_log "ensuring statefulset/${prefix} is at ${expected_nodes} replicas before certificate rotation"
  kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${expected_nodes}" >/dev/null
  wait_for_expected_elasticsearch_pods
}

wait_for_rotated_cluster_health() {
  local elastic_password=""
  local deadline=$((SECONDS + 120))
  local code=""

  elastic_password="$(read_secret_password)"

  while (( SECONDS < deadline )); do
    code="$(
      kubectl -n "${ns}" exec "${curl_pod}" -- \
        curl -sS --max-time 10 --cacert "/etc/${ca_cm}/ca.crt" \
        -u "elastic:${elastic_password}" \
        -o /dev/null -w '%{http_code}' \
        "https://${service}.${ns}.svc:9200/_cluster/health?wait_for_status=yellow&timeout=5s" \
        2>/dev/null || true
    )"
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
    sleep 2
  done

  static_solver_fail "timed out waiting for rotated HTTPS health on ${service}.${ns}.svc"
}

kubectl -n "${ns}" exec openssl-toolbox -- /bin/sh -c "
set -e
rm -rf /tmp/rotated && mkdir -p /tmp/rotated
cat > /tmp/rotated/openssl.cnf <<EOF
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=${service}
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
DNS.2=${service}
DNS.3=${prefix}
DNS.4=*.svc
DNS.5=*.svc.cluster.local
EOF
openssl genrsa -out /tmp/rotated/ca.key 2048
openssl req -x509 -new -nodes -key /tmp/rotated/ca.key -sha256 -days 365 \
  -subj '/CN=es-http-rotated-ca' -out /tmp/rotated/ca.crt
openssl genrsa -out /tmp/rotated/tls.key 2048
openssl req -new -key /tmp/rotated/tls.key -out /tmp/rotated/tls.csr \
  -config /tmp/rotated/openssl.cnf
openssl x509 -req -in /tmp/rotated/tls.csr -CA /tmp/rotated/ca.crt \
  -CAkey /tmp/rotated/ca.key -CAcreateserial -out /tmp/rotated/tls.crt \
  -days 365 -extensions v3_req -extfile /tmp/rotated/openssl.cnf
"

kubectl -n "${ns}" cp openssl-toolbox:/tmp/rotated "${tmp_dir}/rotated"

kubectl -n "${ns}" create secret generic "${secret}" \
  --from-file=tls.crt="${tmp_dir}/rotated/tls.crt" \
  --from-file=tls.key="${tmp_dir}/rotated/tls.key" \
  --from-file=ca.crt="${tmp_dir}/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" create configmap "${ca_cm}" \
  --from-file=ca.crt="${tmp_dir}/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

ensure_http_service_selector
ensure_expected_topology
kubectl -n "${ns}" rollout restart "statefulset/${prefix}"
kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s
wait_for_expected_elasticsearch_pods

kubectl -n "${ns}" delete pod "${curl_pod}" --ignore-not-found=true --wait=true --timeout=120s >/dev/null || true

curl_manifest="${STATIC_SOLVER_STAGE_DIR}/curl-test.yaml"
cat > "${curl_manifest}" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${curl_pod}
  namespace: ${ns}
spec:
  containers:
    - name: curl
      image: curlimages/curl:8.6.0
      command: ["sleep", "infinity"]
      volumeMounts:
        - name: es-http-ca
          mountPath: /etc/${ca_cm}
          readOnly: true
  volumes:
    - name: es-http-ca
      configMap:
        name: ${ca_cm}
        items:
          - key: ca.crt
            path: ca.crt
EOF

kubectl -n "${ns}" apply -f "${curl_manifest}"
kubectl -n "${ns}" wait --for=condition=ready "pod/${curl_pod}" --timeout=300s
wait_for_rotated_cluster_health

static_solver_write_submit "rotated HTTP CA and leaf certificate"
