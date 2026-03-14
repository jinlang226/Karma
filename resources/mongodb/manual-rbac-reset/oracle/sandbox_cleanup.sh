#!/usr/bin/env bash
set -euo pipefail

ns_file="${ORACLE_SANDBOX_NS_FILE:-oracle_sandbox_ns.txt}"

if [ ! -s "${ns_file}" ]; then
  exit 0
fi

sandbox_ns="$(cat "${ns_file}")"
kubectl delete ns "${sandbox_ns}" --ignore-not-found=true --wait=false || true
rm -f "${ns_file}"
