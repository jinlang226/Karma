#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/scale-up-new-nodeset
# Strategy: native_shell
# Notes: The inherited cluster is TLS-enabled and security-enabled. Create the
# warm nodeset with the live cluster's image and TLS secret instead of reusing
# the stale insecure imported manifest.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
index="${BENCH_PARAM_INDEX_NAME:-app-data}"
expected="${BENCH_PARAM_EXPECTED_NODES:-5}"
original="${BENCH_PARAM_ORIGINAL_REPLICAS:-3}"
nodeset="${prefix}-warm"
configmap="${nodeset}-config"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"
new_replicas=$((expected - original))

if [[ "${new_replicas}" -le 0 ]]; then
  static_solver_fail "expected_nodes (${expected}) must exceed original_replicas (${original})"
fi

image="$(kubectl -n "${ns}" get "statefulset/${prefix}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
[[ -n "${image}" ]] || static_solver_fail "failed to detect Elasticsearch image from statefulset/${prefix}"

tls_secret="$(kubectl -n "${ns}" get "statefulset/${prefix}" -o jsonpath='{.spec.template.spec.volumes[?(@.name=="http-certs")].secret.secretName}')"
if [[ -z "${tls_secret}" ]]; then
  tls_secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
fi

elastic_password=""
if password_b64="$(kubectl -n "${ns}" get secret "${password_secret}" -o "jsonpath={.data.${password_key}}" 2>/dev/null)"; then
  if [[ -n "${password_b64}" ]]; then
    elastic_password="$(python3 - "${password_b64}" <<'PY'
import base64
import sys

value = sys.argv[1].strip()
print(base64.b64decode(value).decode() if value else "", end="")
PY
)"
  fi
fi

auth_args=()
if [[ -n "${elastic_password}" ]]; then
  auth_args=(-u "elastic:${elastic_password}")
fi

probe_scheme() {
  local scheme="${1}"
  local code=""
  if ! code="$(
    kubectl -n "${ns}" exec curl-test -- \
      curl -s -S -k -o /dev/null -w '%{http_code}' --max-time 5 \
      "${auth_args[@]}" "${scheme}://${service}:9200/" 2>/dev/null
  )"; then
    return 1
  fi
  [[ "${code}" =~ ^[0-9]+$ ]] && [[ "${code}" != "000" ]]
}

scheme="https"
if ! probe_scheme "${scheme}"; then
  scheme="http"
  probe_scheme "${scheme}" || static_solver_fail "failed to detect a live Elasticsearch HTTP scheme for ${service}"
fi

cat <<YAML | kubectl -n "${ns}" apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${configmap}
data:
  elasticsearch.yml: |
    cluster.name: ${prefix}
    node.name: \${POD_NAME}
    node.roles: [ data, ingest ]
    node.attr.tier: warm
    network.host: 0.0.0.0
    discovery.seed_hosts: [ "${prefix}" ]
    node.store.allow_mmap: false
    xpack.security.enabled: true
    xpack.security.enrollment.enabled: false
    xpack.security.http.ssl.enabled: true
    xpack.security.http.ssl.key: http-certs/tls.key
    xpack.security.http.ssl.certificate: http-certs/tls.crt
    xpack.security.http.ssl.certificate_authorities: [ "http-certs/ca.crt" ]
    xpack.security.transport.ssl.enabled: true
    xpack.security.transport.ssl.verification_mode: certificate
    xpack.security.transport.ssl.key: http-certs/tls.key
    xpack.security.transport.ssl.certificate: http-certs/tls.crt
    xpack.security.transport.ssl.certificate_authorities: [ "http-certs/ca.crt" ]
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${nodeset}
spec:
  serviceName: ${prefix}
  podManagementPolicy: Parallel
  replicas: ${new_replicas}
  selector:
    matchLabels:
      app: ${nodeset}
  template:
    metadata:
      labels:
        app: ${nodeset}
    spec:
      securityContext:
        fsGroup: 1000
      containers:
        - name: elasticsearch
          image: ${image}
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: ES_JAVA_OPTS
              value: -Xms512m -Xmx512m
            - name: ELASTIC_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ${password_secret}
                  key: ${password_key}
          ports:
            - name: http
              containerPort: 9200
            - name: transport
              containerPort: 9300
          readinessProbe:
            tcpSocket:
              port: http
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 12
          volumeMounts:
            - name: es-config
              mountPath: /usr/share/elasticsearch/config/elasticsearch.yml
              subPath: elasticsearch.yml
            - name: http-certs
              mountPath: /usr/share/elasticsearch/config/http-certs
              readOnly: true
            - name: data
              mountPath: /usr/share/elasticsearch/data
          resources:
            requests:
              cpu: 300m
              memory: 1Gi
            limits:
              cpu: "1"
              memory: "2Gi"
      volumes:
        - name: es-config
          configMap:
            name: ${configmap}
        - name: http-certs
          secret:
            secretName: ${tls_secret}
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 2Gi
YAML

static_solver_log "skipping rollout watch; using direct pod readiness polling for ${nodeset}"

python3 - "${ns}" "${nodeset}" "${new_replicas}" <<'PY'
import json
import subprocess
import sys
import time

ns, nodeset, replicas = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.monotonic() + 1200

while time.monotonic() < deadline:
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            ns,
            "get",
            "pods",
            "-l",
            f"app={nodeset}",
            "-o",
            "json",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        time.sleep(5)
        continue

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        time.sleep(5)
        continue

    items = payload.get("items", [])
    if len(items) != replicas:
        time.sleep(5)
        continue

    ready = 0
    for item in items:
        statuses = item.get("status", {}).get("conditions", [])
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in statuses):
            ready += 1

    if ready == replicas:
        sys.exit(0)

    time.sleep(5)

print(f"Timed out waiting for {replicas} ready pods for {nodeset} in {ns}", file=sys.stderr)
sys.exit(1)
PY

index_ready=false
for _ in $(seq 1 20); do
  if kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    "${scheme}://${service}:9200/${index}/_count" >/dev/null 2>&1; then
    index_ready=true
    break
  fi

  kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    -XPUT "${scheme}://${service}:9200/${index}" \
    -H 'Content-Type: application/json' \
    -d '{"settings":{"number_of_shards":3,"number_of_replicas":1}}' >/dev/null 2>&1 || true
  kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    -XPOST "${scheme}://${service}:9200/${index}/_doc/1?refresh=true" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"alpha"}' >/dev/null 2>&1 || true
  kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    -XPOST "${scheme}://${service}:9200/${index}/_doc/2?refresh=true" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"beta"}' >/dev/null 2>&1 || true
  kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    -XPOST "${scheme}://${service}:9200/${index}/_doc/3?refresh=true" \
    -H 'Content-Type: application/json' \
    -d '{"msg":"gamma"}' >/dev/null 2>&1 || true
  sleep 3
done

[[ "${index_ready}" == "true" ]] || static_solver_fail "failed to create or verify index ${index}"

kubectl -n "${ns}" exec curl-test -- \
  curl -s -S -k --max-time 20 "${auth_args[@]}" \
  -XPUT "${scheme}://${service}:9200/${index}/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index.routing.allocation.require.tier":"warm"}' >/dev/null

relocated=false
for _ in $(seq 1 120); do
  if kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    "${scheme}://${service}:9200/_cat/shards/${index}?format=json" |
    python3 -c '
import json, sys
shards = json.load(sys.stdin)
raise SystemExit(0 if any("-warm-" in str(s.get("node") or "") for s in shards) else 1)
'; then
    relocated=true
    break
  fi
  sleep 3
done

[[ "${relocated}" == "true" ]] || static_solver_fail "timed out waiting for ${index} shards to relocate onto ${nodeset}"

cluster_ready=false
for _ in $(seq 1 60); do
  if kubectl -n "${ns}" exec curl-test -- \
    curl -s -S -k --max-time 20 "${auth_args[@]}" \
    "${scheme}://${service}:9200/_cluster/health?wait_for_status=yellow&wait_for_nodes=${expected}&timeout=10s" \
    >/dev/null; then
    cluster_ready=true
    break
  fi
  sleep 5
done

[[ "${cluster_ready}" == "true" ]] || static_solver_fail "cluster did not reach yellow/green with ${expected} nodes"

static_solver_write_submit "added a secure warm nodeset and moved shards"
