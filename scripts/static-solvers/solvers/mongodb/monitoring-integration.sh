#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/monitoring-integration
# Strategy: native_shell
# Notes: Configure Prometheus against the live MongoDB topology, including
# inherited auth/TLS state from earlier workflow stages.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
prom_ns="${BENCH_NS_MONITORING:-monitoring}"
configured_cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
exporter_name="${BENCH_PARAM_EXPORTER_DEPLOYMENT_NAME:-mongodb-exporter}"
prom_deploy="${BENCH_PARAM_PROMETHEUS_DEPLOYMENT_NAME:-prometheus}"
prom_config="${BENCH_PARAM_PROMETHEUS_CONFIGMAP_NAME:-prometheus-config}"
metrics_query="${BENCH_PARAM_METRICS_QUERY:-mongodb_up}"
metrics_port="${BENCH_PARAM_METRICS_PORT:-9216}"
metrics_path="${BENCH_PARAM_METRICS_PATH:-/metrics}"
tls_ca_secret="${BENCH_PARAM_TLS_CA_SECRET_NAME:-mongodb-tls-ca}"
tls_cert_secret="${BENCH_PARAM_TLS_CERT_SECRET_NAME:-mongodb-tls-cert}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
statefulset_json="${tmp_dir}/statefulset.json"
exporter_list_json="${tmp_dir}/exporter-list.json"
prometheus_config_json="${tmp_dir}/prometheus-config.json"

cluster="${configured_cluster}"
if ! kubectl -n "${ns}" get statefulset "${cluster}" >/dev/null 2>&1; then
  cluster="$(kubectl -n "${ns}" get statefulset -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[[ -n "${cluster}" ]] || static_solver_fail "unable to locate a MongoDB statefulset in namespace ${ns}"

kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"
service_name="$(
  python3 - "${statefulset_json}" "${cluster}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
cluster = sys.argv[2]
spec = payload.get("spec") or {}
print(spec.get("serviceName") or f"{cluster}-svc")
PY
)"
mongo_host="${cluster}-0.${service_name}.${ns}.svc.cluster.local:27017"

use_auth="0"
admin_password=""
if kubectl -n "${ns}" get secret "${admin_secret}" >/dev/null 2>&1; then
  admin_password="$(
    kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
  )"
  if [[ -n "${admin_password}" ]]; then
    use_auth="1"
  fi
fi

use_tls="0"
mount_client_pem="0"
if kubectl -n "${ns}" exec "${cluster}-0" -- /bin/sh -c 'test -f /etc/tls/ca.crt || test -f /etc/mongo-ca/ca.crt || test -f /etc/mongodb/tls/ca.crt || test -f /etc/ssl/mongodb/ca.crt' >/dev/null 2>&1; then
  use_tls="1"
  if kubectl -n "${ns}" get secret "${tls_cert_secret}" -o jsonpath='{.data.client\.pem}' >/dev/null 2>&1; then
    mount_client_pem="1"
  fi
fi

python3 - "${exporter_list_json}" "${ns}" "${mongo_host}" "${exporter_name}" "${metrics_port}" "${metrics_path}" \
  "${use_auth}" "${admin_user}" "${admin_secret}" "${use_tls}" "${tls_ca_secret}" "${tls_cert_secret}" "${mount_client_pem}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
namespace = sys.argv[2]
mongo_host = sys.argv[3]
exporter_name = sys.argv[4]
metrics_port = int(sys.argv[5])
metrics_path = sys.argv[6]
use_auth = sys.argv[7] == "1"
admin_user = sys.argv[8]
admin_secret = sys.argv[9]
use_tls = sys.argv[10] == "1"
tls_ca_secret = sys.argv[11]
tls_cert_secret = sys.argv[12]
mount_client_pem = sys.argv[13] == "1"

uri = f"mongodb://{mongo_host}/admin"
if use_tls:
    params = ["ssl=true", "tlsCAFile=/etc/mongo-ca/ca.crt", "tlsAllowInvalidHostnames=true"]
    if mount_client_pem:
        params.append("tlsCertificateKeyFile=/etc/mongo-cert/client.pem")
    uri = f"{uri}?{'&'.join(params)}"

env = [{"name": "MONGODB_URI", "value": uri}]
if use_auth:
    env.append({"name": "MONGODB_USER", "value": admin_user})
    env.append(
        {
            "name": "MONGODB_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {
                    "name": admin_secret,
                    "key": "password",
                }
            },
        }
    )

volume_mounts = []
volumes = []
if use_tls:
    volume_mounts.append({"name": "mongo-ca", "mountPath": "/etc/mongo-ca", "readOnly": True})
    volumes.append({"name": "mongo-ca", "secret": {"secretName": tls_ca_secret}})
    if mount_client_pem:
        volume_mounts.append({"name": "mongo-cert", "mountPath": "/etc/mongo-cert", "readOnly": True})
        volumes.append({"name": "mongo-cert", "secret": {"secretName": tls_cert_secret}})

container = {
    "name": "exporter",
    "image": "percona/mongodb_exporter:0.40.0",
    "args": [
        "--collect-all",
        "--compatible-mode",
        "--mongodb.direct-connect",
        f"--web.listen-address=:{metrics_port}",
        f"--web.telemetry-path={metrics_path}",
    ],
    "env": env,
    "ports": [{"name": "metrics", "containerPort": metrics_port}],
}
if volume_mounts:
    container["volumeMounts"] = volume_mounts

deployment = {
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {"name": exporter_name, "namespace": namespace},
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"app": exporter_name}},
        "template": {
            "metadata": {"labels": {"app": exporter_name}},
            "spec": {
                "containers": [container],
            },
        },
    },
}
if volumes:
    deployment["spec"]["template"]["spec"]["volumes"] = volumes

service = {
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {"name": exporter_name, "namespace": namespace},
    "spec": {
        "selector": {"app": exporter_name},
        "ports": [{"name": "metrics", "port": metrics_port, "targetPort": metrics_port}],
    },
}

output_path.write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": [deployment, service]}))
PY

kubectl apply -f "${exporter_list_json}"
kubectl -n "${ns}" rollout status "deployment/${exporter_name}" --timeout=300s

python3 - "${prometheus_config_json}" "${prom_config}" "${exporter_name}" "${ns}" "${metrics_port}" "${metrics_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
config_name = sys.argv[2]
exporter_name = sys.argv[3]
namespace = sys.argv[4]
metrics_port = int(sys.argv[5])
metrics_path = sys.argv[6]

prometheus_yml = "\n".join(
    [
        "global:",
        "  scrape_interval: 5s",
        "scrape_configs:",
        "  - job_name: prometheus",
        "    static_configs:",
        "      - targets: [\"localhost:9090\"]",
        "  - job_name: mongodb",
        f"    metrics_path: {metrics_path}",
        "    static_configs:",
        f"      - targets: [\"{exporter_name}.{namespace}.svc:{metrics_port}\"]",
        "",
    ]
)

configmap = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": config_name},
    "data": {"prometheus.yml": prometheus_yml},
}
output_path.write_text(json.dumps(configmap))
PY

kubectl -n "${prom_ns}" apply -f "${prometheus_config_json}"
kubectl -n "${prom_ns}" rollout restart "deployment/${prom_deploy}"
kubectl -n "${prom_ns}" rollout status "deployment/${prom_deploy}" --timeout=300s

for _ in $(seq 1 60); do
  if BENCH_NAMESPACE="${ns}" \
    BENCH_NS_MONITORING="${prom_ns}" \
    BENCH_PARAM_METRICS_QUERY="${metrics_query}" \
    BENCH_PARAM_METRICS_PORT="${metrics_port}" \
    BENCH_PARAM_METRICS_PATH="${metrics_path}" \
    python3 "${STATIC_SOLVER_REPO_ROOT}/cases/mongodb/monitoring-integration/oracle/oracle.py" --check all >/dev/null 2>&1; then
    static_solver_write_submit "configured MongoDB monitoring"
    exit 0
  fi
  sleep 5
done

static_solver_fail "Prometheus did not begin scraping MongoDB exporter metrics"
