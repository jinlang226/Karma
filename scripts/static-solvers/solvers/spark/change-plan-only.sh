#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/change-plan-only
# Strategy: native_shell
# Notes: Review-only planner. It snapshots the live SparkPi namespace into
# ConfigMap/change-plan without mutating the cluster.

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
capture_cmd configmaps.txt kubectl -n "${ns}" get configmaps,pods

plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"
python3 - "${tmp_dir}" > "${plan_path}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

tmp = Path(sys.argv[1])
default_image = "apache/spark:3.5.3"

lines: list[str] = []
lines.append("# Spark Change / Migration Plan")
lines.append("")
lines.append("## Scope")
lines.append("- Namespace: `spark-pi`")
lines.append("- Review-only artifact; do not apply changes now.")
lines.append("")
lines.append("## Proposed Next Change Window")
lines.append("1. Reconfirm the current Spark master/worker deployment state, job status, and RBAC manifests immediately before the window.")
lines.append(f"2. Review whether any deployment images should be normalized back to `{default_image}`.")
lines.append("3. Review whether worker/master resource limits or replica counts should be reverted in a controlled rollout.")
lines.append("4. Review job manifests and RBAC policy changes separately, then apply only approved updates.")
lines.append("")
lines.append("## Commands To Review (Do Not Run Now)")
lines.append("```bash")
lines.append("kubectl -n spark-pi get deploy spark-master spark-worker -o yaml > /tmp/spark-pi.deployments.yaml")
lines.append("kubectl -n spark-pi get jobs -o yaml > /tmp/spark-pi.jobs.yaml")
lines.append("kubectl -n spark-pi get role,rolebinding,serviceaccount -o yaml > /tmp/spark-pi.rbac.yaml")
lines.append("kubectl -n spark-pi apply -f /tmp/spark-pi.deployments.yaml")
lines.append("kubectl -n spark-pi apply -f /tmp/spark-pi.jobs.yaml")
lines.append("kubectl -n spark-pi apply -f /tmp/spark-pi.rbac.yaml")
lines.append("```")
lines.append("")
lines.append("## Deployment Snapshot")
lines.append("```yaml")
lines.extend((tmp / "deploys.yaml").read_text(errors="ignore").splitlines())
lines.append("```")
lines.append("")
lines.append("## Job Snapshot")
lines.append("```yaml")
lines.extend((tmp / "jobs.yaml").read_text(errors="ignore").splitlines())
lines.append("```")
lines.append("")
lines.append("## RBAC Snapshot")
lines.append("```yaml")
lines.extend((tmp / "rbac.yaml").read_text(errors="ignore").splitlines())
lines.append("```")
lines.append("")
lines.append("## ConfigMap / Pod Inventory")
lines.append("```text")
lines.extend((tmp / "configmaps.txt").read_text(errors="ignore").splitlines())
lines.append("```")
print("\n".join(lines))
PY

kubectl -n "${ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared change-plan ConfigMap"
