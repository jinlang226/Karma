#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/file-realm-user-roles-merge
# Strategy: native_shell
# Notes: On chained workflows the live cluster often comes from deploy-core-cluster,
# which does not mount the file realm or enable the file/native realm ordering in
# es-config. This solver merges the secrets and patches the live cluster additively.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
aggregate="${BENCH_PARAM_AGGREGATE_SECRET_NAME:-es-file-realm-aggregate}"
provided="${BENCH_PARAM_PROVIDED_SECRET_NAME:-user-provided-file-realm}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
ops_user="${BENCH_PARAM_OPS_USER:-ops-user}"
ops_password="${BENCH_PARAM_OPS_PASSWORD:-opspass}"
report_user="${BENCH_PARAM_REPORT_USER:-report-user}"
report_role="${BENCH_PARAM_REPORT_ROLE:-reporting}"
seed_index="${BENCH_PARAM_SEED_INDEX_NAME:-app-data}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
configmap_name="es-config"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

for field in users users_roles roles.yml; do
  json_field="$(printf '%s' "${field}" | sed 's/\./\\./g')"
  kubectl -n "${ns}" get secret "${aggregate}" -o "jsonpath={.data.${json_field}}" | base64 -d > "${tmp_dir}/aggregate-${field}"
  kubectl -n "${ns}" get secret "${provided}" -o "jsonpath={.data.${json_field}}" | base64 -d > "${tmp_dir}/provided-${field}"
done

python3 - "${tmp_dir}" "${report_user}" "${report_role}" "${seed_index}" <<'PY'
from pathlib import Path
import sys

tmp = Path(sys.argv[1])
report_user = sys.argv[2]
report_role = sys.argv[3]
seed_index = sys.argv[4]


def merge_users(*texts: str) -> str:
    merged = {}
    order = []
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            user, rest = line.split(":", 1)
            user = user.strip()
            if user not in merged:
                order.append(user)
            merged[user] = rest.strip()
    return "\n".join(f"{user}:{merged[user]}" for user in order) + "\n"


def merge_users_roles(*texts: str) -> str:
    merged = {}
    order = []
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            role, users_text = line.split(":", 1)
            role = role.strip()
            if role not in merged:
                merged[role] = []
                order.append(role)
            seen = set(merged[role])
            for user in users_text.split(","):
                user = user.strip()
                if user and user not in seen:
                    merged[role].append(user)
                    seen.add(user)
    if report_role not in merged:
        merged[report_role] = []
        order.append(report_role)
    if report_user not in merged[report_role]:
        merged[report_role].append(report_user)
    lines = []
    for role in order:
        lines.append(f"{role}:{','.join(merged[role])}")
    return "\n".join(lines) + "\n"


def merge_roles_yaml(*texts: str) -> str:
    chunks = []
    for text in texts:
        stripped = text.strip()
        if stripped:
            chunks.append(stripped)
    role_block = (
        f"{report_role}:\n"
        "  cluster: [ 'monitor' ]\n"
        "  indices:\n"
        f"    - names: [ '{seed_index}' ]\n"
        "      privileges: [ 'read', 'view_index_metadata' ]"
    )
    merged = "\n\n".join(chunks) if chunks else ""
    if f"{report_role}:" not in merged:
        merged = f"{merged}\n\n{role_block}".strip()
    return merged + "\n"


(tmp / "users").write_text(
    merge_users(
        (tmp / "aggregate-users").read_text(),
        (tmp / "provided-users").read_text(),
    )
)
(tmp / "users_roles").write_text(
    merge_users_roles(
        (tmp / "aggregate-users_roles").read_text(),
        (tmp / "provided-users_roles").read_text(),
    )
)
(tmp / "roles.yml").write_text(
    merge_roles_yaml(
        (tmp / "aggregate-roles.yml").read_text(),
        (tmp / "provided-roles.yml").read_text(),
    )
)
PY

kubectl -n "${ns}" create secret generic "${aggregate}" \
  --from-file=users="${tmp_dir}/users" \
  --from-file=users_roles="${tmp_dir}/users_roles" \
  --from-file=roles.yml="${tmp_dir}/roles.yml" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" get configmap "${configmap_name}" -o "jsonpath={.data.elasticsearch\.yml}" > "${tmp_dir}/elasticsearch.yml"

python3 - "${tmp_dir}/elasticsearch.yml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text().splitlines()
required = {
    "xpack.security.enabled": "true",
    "xpack.security.authc.realms.file.file1.order": "0",
    "xpack.security.authc.realms.native.native1.order": "1",
}

seen = set()
updated = []
for raw in lines:
    stripped = raw.strip()
    replaced = False
    for key, value in required.items():
        prefix = f"{key}:"
        if stripped.startswith(prefix):
            updated.append(f"{key}: {value}")
            seen.add(key)
            replaced = True
            break
    if not replaced:
        updated.append(raw)

for key, value in required.items():
    if key not in seen:
        updated.append(f"{key}: {value}")

path.write_text("\n".join(updated) + "\n")
PY

kubectl -n "${ns}" create configmap "${configmap_name}" \
  --from-file=elasticsearch.yml="${tmp_dir}/elasticsearch.yml" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" get "statefulset/${prefix}" -o json > "${tmp_dir}/statefulset.json"

python3 - "${tmp_dir}/statefulset.json" "${tmp_dir}/statefulset-patch.json" "${aggregate}" <<'PY'
import json
import sys
from pathlib import Path

statefulset = json.loads(Path(sys.argv[1]).read_text())
patch_path = Path(sys.argv[2])
aggregate = sys.argv[3]

spec = statefulset["spec"]["template"]["spec"]
volumes = spec.setdefault("volumes", [])
containers = spec.get("containers", [])

container_index = None
for idx, container in enumerate(containers):
    if container.get("name") == "elasticsearch":
        container_index = idx
        break

if container_index is None:
    raise SystemExit("statefulset does not contain elasticsearch container")

desired_volume = {
    "name": "file-realm",
    "secret": {
        "secretName": aggregate,
        "items": [
            {"key": "users", "path": "users"},
            {"key": "users_roles", "path": "users_roles"},
            {"key": "roles.yml", "path": "roles.yml"},
        ],
    },
}

desired_mounts = [
    {
        "name": "file-realm",
        "mountPath": "/usr/share/elasticsearch/config/users",
        "subPath": "users",
        "readOnly": True,
    },
    {
        "name": "file-realm",
        "mountPath": "/usr/share/elasticsearch/config/users_roles",
        "subPath": "users_roles",
        "readOnly": True,
    },
    {
        "name": "file-realm",
        "mountPath": "/usr/share/elasticsearch/config/roles.yml",
        "subPath": "roles.yml",
        "readOnly": True,
    },
]

ops = []

volume_index = next((idx for idx, volume in enumerate(volumes) if volume.get("name") == "file-realm"), None)
if volume_index is None:
    if "volumes" not in spec:
        ops.append({"op": "add", "path": "/spec/template/spec/volumes", "value": []})
        volume_index = 0
    ops.append({"op": "add", "path": "/spec/template/spec/volumes/-", "value": desired_volume})
else:
    ops.append({"op": "replace", "path": f"/spec/template/spec/volumes/{volume_index}", "value": desired_volume})

container = containers[container_index]
mounts = container.get("volumeMounts")
if mounts is None:
    ops.append(
        {
            "op": "add",
            "path": f"/spec/template/spec/containers/{container_index}/volumeMounts",
            "value": desired_mounts,
        }
    )
else:
    mount_by_path = {mount.get("mountPath"): idx for idx, mount in enumerate(mounts)}
    for desired in desired_mounts:
        mount_index = mount_by_path.get(desired["mountPath"])
        if mount_index is None:
            ops.append(
                {
                    "op": "add",
                    "path": f"/spec/template/spec/containers/{container_index}/volumeMounts/-",
                    "value": desired,
                }
            )
        else:
            ops.append(
                {
                    "op": "replace",
                    "path": f"/spec/template/spec/containers/{container_index}/volumeMounts/{mount_index}",
                    "value": desired,
                }
            )

patch_path.write_text(json.dumps(ops))
PY

kubectl -n "${ns}" patch "statefulset/${prefix}" --type=json -p "$(cat "${tmp_dir}/statefulset-patch.json")"
kubectl -n "${ns}" rollout restart "statefulset/${prefix}"
kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s

scheme=""
for candidate in https http; do
  for _ in $(seq 1 30); do
    code="$(kubectl -n "${ns}" exec "${curl_pod}" -- curl -s -k -o /dev/null -w "%{http_code}" -u "${ops_user}:${ops_password}" --max-time 5 "${candidate}://${service}.${ns}.svc:9200/_cluster/health" 2>/dev/null || echo 000)"
    if [[ "${code}" == "200" ]]; then
      scheme="${candidate}"
      break 2
    fi
    sleep 2
  done
done

if [[ -z "${scheme}" ]]; then
  static_solver_fail "ops user did not become live after file realm rollout"
fi

kubectl -n "${ns}" exec "${curl_pod}" -- \
  curl -s -k -u "${ops_user}:${ops_password}" \
  -XPUT "${scheme}://${service}.${ns}.svc:9200/${seed_index}" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"number_of_shards":1,"number_of_replicas":1}}' >/dev/null || true

kubectl -n "${ns}" exec "${curl_pod}" -- \
  curl -s -k -u "${ops_user}:${ops_password}" \
  -XPOST "${scheme}://${service}.${ns}.svc:9200/${seed_index}/_doc?refresh=true" \
  -H "Content-Type: application/json" \
  -d '{"msg":"seed"}' >/dev/null

static_solver_write_submit "merged file realm users and enabled file realm auth"
