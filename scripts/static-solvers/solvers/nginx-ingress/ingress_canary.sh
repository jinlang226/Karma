#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/ingress_canary
# Strategy: native_shell
# Notes: Normalize the live canary ingress for the workflow-provided header
# value, remove stale conflicting canary annotations, and wait for routing to
# converge before submitting.

static_solver_export_nginx_defaults

app_ns="${BENCH_NS_APP}"
ingress_name="${BENCH_PARAM_CANARY_INGRESS_NAME:-canary-canary}"
host="${BENCH_PARAM_HOST:-canary.example.com}"
header_name="${BENCH_PARAM_HEADER_NAME:-X-Canary}"
header_value="${BENCH_PARAM_HEADER_VALUE:-always}"
stable_body="${BENCH_PARAM_STABLE_BODY:-stable}"
canary_body="${BENCH_PARAM_CANARY_BODY:-canary}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
ingress_json="${tmp_dir}/ingress.json"
patched_ingress_json="${tmp_dir}/ingress-patched.json"

kubectl -n "${app_ns}" get ingress "${ingress_name}" -o json > "${ingress_json}"
python3 - "${ingress_json}" "${patched_ingress_json}" "${host}" "${header_name}" "${header_value}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
host = sys.argv[3]
header_name = sys.argv[4]
header_value = sys.argv[5]

payload = json.loads(source_path.read_text())
payload.pop("status", None)
metadata = payload.get("metadata", {})
for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
    metadata.pop(key, None)

annotations = metadata.setdefault("annotations", {})
for key in (
    "nginx.ingress.kubernetes.io/canary-weight",
    "nginx.ingress.kubernetes.io/canary-by-cookie",
    "nginx.ingress.kubernetes.io/canary-by-header-pattern",
):
    annotations.pop(key, None)
annotations["nginx.ingress.kubernetes.io/canary"] = "true"
annotations["nginx.ingress.kubernetes.io/canary-by-header"] = header_name
annotations["nginx.ingress.kubernetes.io/canary-by-header-value"] = header_value

rules = ((payload.get("spec") or {}).get("rules")) or []
if rules:
    rules[0]["host"] = host

output_path.write_text(json.dumps(payload))
PY

kubectl -n "${app_ns}" apply -f "${patched_ingress_json}"

for _ in $(seq 1 60); do
  if BENCH_PARAM_HOST="${host}" \
    BENCH_PARAM_HEADER_NAME="${header_name}" \
    BENCH_PARAM_HEADER_VALUE="${header_value}" \
    BENCH_PARAM_STABLE_BODY="${stable_body}" \
    BENCH_PARAM_CANARY_BODY="${canary_body}" \
    python3 "${STATIC_SOLVER_REPO_ROOT}/cases/nginx-ingress/ingress_canary/oracle/oracle.py" >/dev/null 2>&1; then
    static_solver_write_submit "fixed header canary routing"
    exit 0
  fi
  sleep 3
done

static_solver_fail "canary ingress routing did not converge for ${header_name}: ${header_value}"
