#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/readonly-audit
# Strategy: native_shell
# Notes: Read-only auditor. It snapshots the live SparkPi namespace into
# ConfigMap/config-audit without mutating the cluster.

static_solver_export_namespace_if_unset "spark-pi"

ns="${BENCH_NAMESPACE}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

capture_cmd() {
  local name="${1:?output name is required}"
  shift
  "$@" > "${tmp_dir}/${name}" 2>&1 || true
}

capture_cmd deploys.yaml kubectl -n "${ns}" get deploy spark-master spark-worker -o yaml
capture_cmd jobs.yaml kubectl -n "${ns}" get jobs -o yaml
capture_cmd rbac.yaml kubectl -n "${ns}" get role,rolebinding,serviceaccount -o yaml
capture_cmd network.txt kubectl -n "${ns}" get networkpolicy,pods,configmaps

findings_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"
python3 - "${tmp_dir}" > "${findings_path}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

tmp = Path(sys.argv[1])

lines: list[str] = []
lines.append("Spark Read-Only Audit")
lines.append("Namespace: spark-pi")
lines.append("")
lines.append("Compliance findings")
lines.append("- Reviewed RBAC roles, bindings, service accounts, Deployments, Jobs, network policies, pods, and ConfigMaps in spark-pi.")
lines.append("- This audit is read-only; no live Spark resources were changed.")
lines.append("- Confirm that image provenance, resource limits, and job security context match the approved baseline before scheduling any maintenance window.")
lines.append("")
lines.append("Deployment snapshot")
lines.extend((tmp / "deploys.yaml").read_text(errors="ignore").splitlines())
lines.append("")
lines.append("Job snapshot")
lines.extend((tmp / "jobs.yaml").read_text(errors="ignore").splitlines())
lines.append("")
lines.append("RBAC snapshot")
lines.extend((tmp / "rbac.yaml").read_text(errors="ignore").splitlines())
lines.append("")
lines.append("Network / pod / ConfigMap inventory")
lines.extend((tmp / "network.txt").read_text(errors="ignore").splitlines())
print("\n".join(lines))
PY

kubectl -n "${ns}" create configmap config-audit \
  --from-file=findings.txt="${findings_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared config-audit ConfigMap"
