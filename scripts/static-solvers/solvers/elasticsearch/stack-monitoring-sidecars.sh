#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/stack-monitoring-sidecars
# Strategy: native_shell
# Notes: Chained workflows inherit the secure Elasticsearch cluster from earlier
# stages, so the standalone apply that would add the Beats sidecars is skipped.
# Patch the live StatefulSet and configmaps in place, then verify monitoring data
# reaches the monitoring namespace.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
tls_secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"
monitoring_service="${BENCH_PARAM_MONITORING_SERVICE_NAME:-monitoring-es-http}"
monitoring_deployment="${BENCH_PARAM_MONITORING_DEPLOYMENT_NAME:-monitoring-es}"
monitoring_curl_pod="${BENCH_PARAM_MONITORING_CURL_POD_NAME:-monitoring-curl-test}"
monitoring_ns="monitoring"
metricbeat_cm="metricbeat-config"
filebeat_cm="filebeat-config"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

write_sidecar_configmaps() {
  cat > "${tmp_dir}/metricbeat.yml" <<EOF
metricbeat.modules:
  - module: elasticsearch
    xpack.enabled: true
    period: 10s
    hosts: ["https://localhost:9200"]
    username: elastic
    password: \${ELASTIC_PASSWORD}
    ssl.certificate_authorities: ["/etc/es-http/ca.crt"]

output.elasticsearch:
  hosts: ["http://${monitoring_service}.${monitoring_ns}.svc:9200"]

logging.to_files: false
logging.to_syslog: false
EOF

  cat > "${tmp_dir}/filebeat.yml" <<EOF
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/es/*.log

output.elasticsearch:
  hosts: ["http://${monitoring_service}.${monitoring_ns}.svc:9200"]

logging.to_files: false
logging.to_syslog: false
EOF

  kubectl -n "${ns}" create configmap "${metricbeat_cm}" \
    --from-file=metricbeat.yml="${tmp_dir}/metricbeat.yml" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
  kubectl -n "${ns}" create configmap "${filebeat_cm}" \
    --from-file=filebeat.yml="${tmp_dir}/filebeat.yml" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
}

patch_es_config() {
  python3 - "${ns}" <<'PY' | kubectl -n "${ns}" apply -f -
import json
import subprocess
import sys

ns = sys.argv[1]
result = subprocess.run(
    ["kubectl", "-n", ns, "get", "configmap", "es-config", "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if result.returncode != 0:
    raise SystemExit(result.stderr.strip() or "failed to read es-config")

obj = json.loads(result.stdout)
lines = obj["data"]["elasticsearch.yml"].splitlines()
updated = []
found = False
for raw in lines:
    stripped = raw.strip()
    if stripped.startswith("path.logs:"):
        updated.append("path.logs: /var/log/es")
        found = True
    else:
        updated.append(raw)
if not found:
    updated.append("path.logs: /var/log/es")

obj["data"]["elasticsearch.yml"] = "\n".join(updated).rstrip() + "\n"
for key in ("managedFields", "resourceVersion", "uid", "creationTimestamp"):
    obj.get("metadata", {}).pop(key, None)
obj.pop("status", None)
print(json.dumps(obj))
PY
}

patch_statefulset() {
  local rollout_tag=""
  rollout_tag="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  python3 - "${ns}" "${cluster}" "${metricbeat_cm}" "${filebeat_cm}" "${tls_secret}" "${password_secret}" "${password_key}" "${rollout_tag}" <<'PY' | kubectl -n "${ns}" apply -f -
import json
import subprocess
import sys

ns, cluster, metricbeat_cm, filebeat_cm, tls_secret, password_secret, password_key, rollout_tag = sys.argv[1:]
result = subprocess.run(
    ["kubectl", "-n", ns, "get", "statefulset", cluster, "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if result.returncode != 0:
    raise SystemExit(result.stderr.strip() or f"failed to read statefulset/{cluster}")

obj = json.loads(result.stdout)
template = obj["spec"]["template"]
template.setdefault("metadata", {}).setdefault("annotations", {})["sidecar-config-hash"] = rollout_tag
spec = template["spec"]
containers = spec.get("containers", [])

main_container = None
for container in containers:
    if container.get("name") == "elasticsearch":
        main_container = container
        break

if main_container is None:
    raise SystemExit("statefulset does not contain elasticsearch container")

main_mounts = main_container.setdefault("volumeMounts", [])
mount_by_path = {mount.get("mountPath"): mount for mount in main_mounts}
mount_by_path["/var/log/es"] = {"name": "es-logs", "mountPath": "/var/log/es"}
mount_by_path["/usr/share/elasticsearch/config/http-certs"] = {
    "name": "http-certs",
    "mountPath": "/usr/share/elasticsearch/config/http-certs",
    "readOnly": True,
}
main_container["volumeMounts"] = list(mount_by_path.values())

env_list = main_container.setdefault("env", [])
env_by_name = {entry.get("name"): entry for entry in env_list}
env_by_name["ELASTIC_PASSWORD"] = {
    "name": "ELASTIC_PASSWORD",
    "valueFrom": {"secretKeyRef": {"name": password_secret, "key": password_key}},
}
main_container["env"] = list(env_by_name.values())

metricbeat = {
    "name": "metricbeat",
    "image": "docker.elastic.co/beats/metricbeat:7.17.9",
    "env": [
        {
            "name": "ELASTIC_PASSWORD",
            "valueFrom": {"secretKeyRef": {"name": password_secret, "key": password_key}},
        }
    ],
    "command": ["/bin/sh", "-c", "metricbeat -e -strict.perms=false"],
    "volumeMounts": [
        {
            "name": "metricbeat-config",
            "mountPath": "/usr/share/metricbeat/metricbeat.yml",
            "subPath": "metricbeat.yml",
            "readOnly": True,
        },
        {
            "name": "http-certs",
            "mountPath": "/etc/es-http",
            "readOnly": True,
        },
    ],
}

filebeat = {
    "name": "filebeat",
    "image": "docker.elastic.co/beats/filebeat:7.17.9",
    "command": ["/bin/sh", "-c", "filebeat -e -strict.perms=false"],
    "volumeMounts": [
        {
            "name": "filebeat-config",
            "mountPath": "/usr/share/filebeat/filebeat.yml",
            "subPath": "filebeat.yml",
            "readOnly": True,
        },
        {
            "name": "es-logs",
            "mountPath": "/var/log/es",
        },
    ],
}

other = [container for container in containers if container.get("name") not in {"metricbeat", "filebeat"}]
main = [container for container in other if container.get("name") == "elasticsearch"]
rest = [container for container in other if container.get("name") != "elasticsearch"]
spec["containers"] = main + rest + [metricbeat, filebeat]

volumes = spec.setdefault("volumes", [])
volume_by_name = {volume.get("name"): volume for volume in volumes}
volume_by_name["http-certs"] = {"name": "http-certs", "secret": {"secretName": tls_secret}}
volume_by_name["metricbeat-config"] = {
    "name": "metricbeat-config",
    "configMap": {
        "name": metricbeat_cm,
        "items": [{"key": "metricbeat.yml", "path": "metricbeat.yml"}],
    },
}
volume_by_name["filebeat-config"] = {
    "name": "filebeat-config",
    "configMap": {
        "name": filebeat_cm,
        "items": [{"key": "filebeat.yml", "path": "filebeat.yml"}],
    },
}
volume_by_name["es-logs"] = {"name": "es-logs", "emptyDir": {}}
spec["volumes"] = list(volume_by_name.values())

for key in ("managedFields", "resourceVersion", "uid", "creationTimestamp"):
    obj.get("metadata", {}).pop(key, None)
obj.pop("status", None)
print(json.dumps(obj))
PY
}

wait_for_sidecar_rollout() {
  local target_revision=""
  local revision_deadline=$((SECONDS + 60))

  while (( SECONDS < revision_deadline )); do
    target_revision="$(
      kubectl -n "${ns}" get "statefulset/${cluster}" -o jsonpath='{.status.updateRevision}' 2>/dev/null || true
    )"
    if [[ -n "${target_revision}" ]]; then
      break
    fi
    sleep 2
  done

  [[ -n "${target_revision}" ]] || static_solver_fail "statefulset/${cluster} did not report an update revision"

  python3 - "${ns}" "${cluster}" "${target_revision}" <<'PY'
import json
import subprocess
import sys
import time

ns, cluster, target_revision = sys.argv[1], sys.argv[2], sys.argv[3]
deadline = time.monotonic() + 900
last_error = ""

while time.monotonic() < deadline:
    result = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={cluster}", "-o", "json"],
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
    if len(items) != 3:
        last_error = f"expected 3 Elasticsearch pods, got {len(items)}"
        time.sleep(5)
        continue

    bad = []
    for item in items:
        name = item.get("metadata", {}).get("name", "unknown")
        labels = item.get("metadata", {}).get("labels", {}) or {}
        revision = labels.get("controller-revision-hash", "")
        containers = {container.get("name") for container in item.get("spec", {}).get("containers", [])}
        if revision != target_revision:
            bad.append(f"{name} revision={revision or 'missing'}")
            continue
        if not {"metricbeat", "filebeat"}.issubset(containers):
            bad.append(f"{name} missing sidecars")
            continue
        conditions = item.get("status", {}).get("conditions", []) or []
        ready = any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions)
        if not ready:
            bad.append(f"{name} not Ready")

    if not bad:
        raise SystemExit(0)

    last_error = ", ".join(bad)
    time.sleep(5)

print(last_error or "timed out waiting for Elasticsearch sidecar rollout", file=sys.stderr)
raise SystemExit(1)
PY
}

wait_for_monitoring_indices() {
  kubectl -n "${monitoring_ns}" wait --for=condition=Ready "pod/${monitoring_curl_pod}" --timeout=300s >/dev/null
  kubectl -n "${monitoring_ns}" rollout status "deployment/${monitoring_deployment}" --timeout=300s >/dev/null

  python3 - "${monitoring_ns}" "${monitoring_curl_pod}" "${monitoring_service}" <<'PY'
import json
import subprocess
import sys
import time

mon_ns, curl_pod, service = sys.argv[1], sys.argv[2], sys.argv[3]
deadline = time.monotonic() + 600
last_error = "monitoring indices did not appear"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path):
    result = run(
        [
            "kubectl",
            "-n",
            mon_ns,
            "exec",
            curl_pod,
            "--",
            "curl",
            "-s",
            "-S",
            "--max-time",
            "10",
            f"http://{service}.{mon_ns}.svc:9200{path}",
        ]
    )
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
    indices, detail = curl_json("/_cat/indices?format=json")
    if not isinstance(indices, list):
        last_error = f"failed to query monitoring indices: {detail}"
        time.sleep(5)
        continue

    monitoring = [item for item in indices if item.get("index", "").startswith(".monitoring-es")]
    if not monitoring:
        last_error = "monitoring indices not found"
        time.sleep(5)
        continue

    has_docs = False
    for item in monitoring:
        count, detail = curl_json(f"/{item['index']}/_count")
        if isinstance(count, dict) and isinstance(count.get("count"), int) and count["count"] > 0:
            has_docs = True
            break
    if has_docs:
        raise SystemExit(0)

    last_error = "monitoring indices exist but have no documents"
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

write_sidecar_configmaps
patch_es_config
patch_statefulset
wait_for_sidecar_rollout
wait_for_monitoring_indices

static_solver_write_submit "restored Elasticsearch stack monitoring sidecars"
