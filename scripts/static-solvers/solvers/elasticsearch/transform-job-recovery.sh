#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/transform-job-recovery
# Strategy: native_shell
# Notes: Chained workflows often skip the additive fixture that should create the
# transform-capacity nodeset and checkpoint baseline. Rebuild those missing
# pieces solver-side, restore transform capacity, and wait for the checkpoint to
# advance without deleting the destination index.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
transform_prefix="${BENCH_PARAM_TRANSFORM_CLUSTER_PREFIX:-es-transform}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
source_index="${BENCH_PARAM_SOURCE_INDEX_NAME:-app-events}"
dest_index="${BENCH_PARAM_DEST_INDEX_NAME:-app-events-rollup}"
transform_id="${BENCH_PARAM_TRANSFORM_ID:-events-by-service}"
checkpoint_cm="${BENCH_PARAM_CHECKPOINT_CONFIGMAP:-transform-checkpoint}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"

service_host="${service}.${ns}.svc"
transform_configmap="${transform_prefix}-config"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

read_secret_password() {
  local secret_name="$1"
  local secret_key="$2"
  local encoded=""

  encoded="$(
    kubectl -n "${ns}" get secret "${secret_name}" -o "jsonpath={.data.${secret_key}}" 2>/dev/null || true
  )"
  if [[ -z "${encoded}" ]]; then
    return 1
  fi

  printf '%s' "${encoded}" | base64 -d
}

elastic_password=""
if elastic_password="$(read_secret_password "${password_secret}" "${password_key}")"; then
  :
fi

probe_scheme() {
  local scheme="$1"
  local output=""

  if ! output="$(
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -sS -k -o /dev/null -w '%{http_code}' --max-time 5 \
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

scheme=""

curl_exec() {
  local use_stdin="false"
  if [[ "${1:-}" == "--stdin" ]]; then
    use_stdin="true"
    shift
  fi

  local -a cmd=(kubectl -n "${ns}" exec)
  if [[ "${use_stdin}" == "true" ]]; then
    cmd+=(-i)
  fi
  cmd+=("${curl_pod}" -- curl -sS -k)
  if [[ -n "${elastic_password}" ]]; then
    cmd+=(-u "elastic:${elastic_password}")
  fi
  cmd+=("$@")
  "${cmd[@]}"
}

curl_json() {
  local path="$1"
  curl_exec --max-time 20 \
    "${scheme}://${service_host}:9200${path}"
}

curl_code() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local -a cmd=(-o /dev/null -w '%{http_code}' --max-time 20 -X "${method}")
  if [[ -n "${data}" ]]; then
    cmd+=(-H 'Content-Type: application/json' -d "${data}")
  fi
  cmd+=("${scheme}://${service_host}:9200${path}")
  curl_exec "${cmd[@]}" 2>/dev/null || printf '000\n'
}

ensure_transform_nodeset() {
  cat > "${tmp_dir}/transform-capacity.yaml" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${transform_configmap}
  namespace: ${ns}
data:
  elasticsearch.yml: |
    cluster.name: ${cluster_prefix}
    node.name: \${POD_NAME}
    node.roles: [ transform, ingest ]
    network.host: 0.0.0.0
    discovery.seed_hosts:
      - ${cluster_prefix}-0.${cluster_prefix}
      - ${cluster_prefix}-1.${cluster_prefix}
      - ${cluster_prefix}-2.${cluster_prefix}
    node.store.allow_mmap: false
    xpack.security.enabled: false
    xpack.security.http.ssl.enabled: false
    xpack.security.transport.ssl.enabled: false
---
apiVersion: v1
kind: Service
metadata:
  name: ${transform_prefix}
  namespace: ${ns}
spec:
  clusterIP: None
  selector:
    app: ${transform_prefix}
  ports:
    - name: transport
      port: 9300
      targetPort: 9300
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${transform_prefix}
  namespace: ${ns}
spec:
  serviceName: ${transform_prefix}
  podManagementPolicy: Parallel
  replicas: 1
  selector:
    matchLabels:
      app: ${transform_prefix}
  template:
    metadata:
      labels:
        app: ${transform_prefix}
    spec:
      containers:
        - name: elasticsearch
          image: docker.elastic.co/elasticsearch/elasticsearch:7.17.9
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: ES_JAVA_OPTS
              value: "-Xms512m -Xmx512m"
          ports:
            - containerPort: 9200
              name: http
            - containerPort: 9300
              name: transport
          readinessProbe:
            tcpSocket:
              port: 9200
            initialDelaySeconds: 10
            periodSeconds: 5
            timeoutSeconds: 1
            failureThreshold: 6
          volumeMounts:
            - name: ${transform_configmap}
              mountPath: /usr/share/elasticsearch/config/elasticsearch.yml
              subPath: elasticsearch.yml
            - name: data
              mountPath: /usr/share/elasticsearch/data
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "1"
              memory: "2Gi"
      volumes:
        - name: ${transform_configmap}
          configMap:
            name: ${transform_configmap}
            items:
              - key: elasticsearch.yml
                path: elasticsearch.yml
        - name: data
          emptyDir: {}
EOF

  kubectl apply -f "${tmp_dir}/transform-capacity.yaml" >/dev/null
  kubectl -n "${ns}" scale "statefulset/${transform_prefix}" --replicas=1 >/dev/null
  kubectl -n "${ns}" delete "pod/${transform_prefix}-0" --ignore-not-found=true >/dev/null || true
  kubectl -n "${ns}" wait --for=condition=Ready "pod/${curl_pod}" --timeout=300s >/dev/null
  kubectl -n "${ns}" wait --for=condition=Ready pod -l "app=${transform_prefix}" --timeout=600s >/dev/null
}

seed_fresh_docs() {
  local payload=""
  payload="$(
    python3 - "${source_index}" <<'PY'
from datetime import datetime, timedelta, timezone
import sys

source_index = sys.argv[1]
base = datetime.now(timezone.utc)
for offset, service in enumerate(("api", "web")):
    ts = (base + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f'{{"index":{{"_index":"{source_index}"}}}}')
    print(f'{{"service":"{service}","@timestamp":"{ts}"}}')
PY
  )"

  printf '%s' "${payload}" | curl_exec --stdin -XPOST \
    "${scheme}://${service_host}:9200/_bulk" \
    -H 'Content-Type: application/x-ndjson' \
    --data-binary @- >/dev/null

  curl_exec -XPOST \
    "${scheme}://${service_host}:9200/${source_index}/_refresh" >/dev/null
}

transform_payload="$(
  python3 - "${source_index}" "${dest_index}" <<'PY'
import json
import sys

source_index, dest_index = sys.argv[1], sys.argv[2]
payload = {
    "source": {"index": source_index},
    "dest": {"index": dest_index},
    "pivot": {
        "group_by": {"service": {"terms": {"field": "service.keyword"}}},
        "aggregations": {"event_count": {"value_count": {"field": "service.keyword"}}},
    },
    "sync": {"time": {"field": "@timestamp", "delay": "1s"}},
}
print(json.dumps(payload, separators=(",", ":")))
PY
)"

recreate_transform() {
  curl_code DELETE "/_transform/${transform_id}" >/dev/null || true

  local create_code=""
  create_code="$(curl_code PUT "/_transform/${transform_id}" "${transform_payload}")"
  if [[ "${create_code}" != "200" && "${create_code}" != "201" ]]; then
    static_solver_fail "failed to create transform ${transform_id} (HTTP ${create_code})"
  fi
}

record_checkpoint_baseline() {
  local checkpoint_before="$1"
  kubectl -n "${ns}" create configmap "${checkpoint_cm}" \
    --from-literal=checkpoint_before="${checkpoint_before}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
}

stop_transform_if_present() {
  curl_code POST "/_transform/${transform_id}/_stop?force=true&wait_for_completion=true" >/dev/null || true
}

start_transform() {
  local start_code=""
  start_code="$(curl_code POST "/_transform/${transform_id}/_start")"
  if [[ "${start_code}" != "200" && "${start_code}" != "409" ]]; then
    static_solver_fail "failed to start transform ${transform_id} (HTTP ${start_code})"
  fi
}

wait_for_transform_advance() {
  local checkpoint_before="$1"
  python3 - "${ns}" "${curl_pod}" "${scheme}" "${service_host}" "${transform_id}" "${dest_index}" "${checkpoint_before}" "${elastic_password}" <<'PY'
import json
import subprocess
import sys
import time

ns, curl_pod, scheme, service_host, transform_id, dest_index, checkpoint_before, password = sys.argv[1:]
checkpoint_before = int(checkpoint_before)
deadline = time.monotonic() + 600
last_error = "transform did not recover"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path):
    cmd = [
        "kubectl",
        "-n",
        ns,
        "exec",
        curl_pod,
        "--",
        "curl",
        "-s",
        "-S",
        "-k",
        "--max-time",
        "20",
    ]
    if password:
        cmd += ["-u", f"elastic:{password}"]
    cmd += [f"{scheme}://{service_host}:9200{path}"]
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return None, detail
    output = result.stdout.strip()
    if not output:
        return None, f"empty response for {path}"
    try:
        return json.loads(output), ""
    except json.JSONDecodeError:
        return None, f"failed to parse JSON from {path}"


while time.monotonic() < deadline:
    errors = []

    stats, detail = curl_json(f"/_transform/{transform_id}/_stats")
    if stats is None:
        errors.append(f"transform stats missing: {detail}")
    else:
        transforms = stats.get("transforms") or []
        if not transforms:
            errors.append("transform stats missing")
        else:
            transform = transforms[0]
            state = transform.get("state") or transform.get("stats", {}).get("state")
            if state != "started":
                errors.append(f"transform state expected started, got {state}")
            checkpoint_now = (
                transform.get("checkpointing", {}).get("last", {}).get("checkpoint")
            )
            if checkpoint_now is None:
                checkpoint_now = (
                    transform.get("stats", {}).get("checkpointing", {}).get("last", {}).get("checkpoint")
                )
            if checkpoint_now is None:
                errors.append("unable to read current checkpoint")
            elif checkpoint_now <= checkpoint_before:
                errors.append(
                    f"checkpoint did not advance (before={checkpoint_before}, now={checkpoint_now})"
                )

    count, detail = curl_json(f"/{dest_index}/_count")
    if count is None:
        errors.append(f"destination index check failed: {detail}")
    elif count.get("count", 0) <= 0:
        errors.append("destination index has no documents")

    if not errors:
        raise SystemExit(0)

    last_error = "; ".join(errors)
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

kubectl -n "${ns}" wait --for=condition=Ready "pod/${curl_pod}" --timeout=300s >/dev/null
scheme="$(detect_scheme)"

ensure_transform_nodeset
stop_transform_if_present
seed_fresh_docs
recreate_transform
record_checkpoint_baseline "0"
start_transform
wait_for_transform_advance "0"

static_solver_write_submit "restored Elasticsearch transform capacity and advanced the checkpoint"
