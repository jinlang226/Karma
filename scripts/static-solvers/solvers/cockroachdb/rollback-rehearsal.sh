#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/rollback-rehearsal
# Strategy: state_capture_shell
# Notes: Prepare a rollback script in a ConfigMap without mutating the live
# cluster configuration.

static_solver_export_namespace_if_unset "cockroachdb"

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
script_path="${STATIC_SOLVER_STAGE_DIR}/rollback.sh"

cluster_flag="--insecure"
if kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
  ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  cluster_flag="--certs-dir=/cockroach/cockroach-certs"
fi

non_default_settings="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT variable FROM crdb_internal.cluster_settings WHERE value != default_value;" \
    2>/dev/null
)"

zone_overrides="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT target, raw_config_sql FROM crdb_internal.zones WHERE raw_config_sql IS NOT NULL AND raw_config_sql != '';" \
    2>/dev/null
)"

replicas="$(
  kubectl -n "${BENCH_NAMESPACE}" get statefulset "${prefix}" \
    -o jsonpath='{.spec.replicas}' 2>/dev/null || true
)"

{
  cat <<EOF
#!/usr/bin/env bash
set -euo pipefail

ns="${BENCH_NAMESPACE}"
pod="${prefix}-0"
flag="--insecure"
if kubectl -n "\${ns}" exec "\${pod}" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  flag="--certs-dir=/cockroach/cockroach-certs"
fi

# Reset non-default cluster settings back to their defaults.
EOF

  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    [[ "${line}" == variable* ]] && continue
    setting_name="${line%%$'\t'*}"
    [[ -z "${setting_name}" ]] && continue
    printf 'kubectl -n "%s" exec "%s" -- ./cockroach sql "${flag}" -e "RESET CLUSTER SETTING %s;"\n' \
      "${BENCH_NAMESPACE}" "${prefix}-0" "${setting_name}"
  done <<<"${non_default_settings}"

  cat <<EOF

# Review zone configuration overrides before running rollback.
EOF

  while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    [[ "${line}" == target* ]] && continue
    printf '# %s\n' "${line}"
  done <<<"${zone_overrides}"

  if [[ -n "${replicas}" ]]; then
    printf '\n# Current StatefulSet replica count for %s: %s\n' "${prefix}" "${replicas}"
  fi
} > "${script_path}"

kubectl -n "${BENCH_NAMESPACE}" create configmap rollback-rehearsal \
  --from-file=rollback.sh="${script_path}" \
  --dry-run=client -o yaml | kubectl apply -f -

static_solver_write_submit "prepared rollback-rehearsal ConfigMap"
