#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/deploy_spark_pi
# Strategy: native_shell
# Notes: Repairs the missing RBAC permission, recreates the corrected SparkPi
# Job from the case manifest, and waits for successful completion. The workflow
# can override the Spark image version, so render the job image and example jar
# path from BENCH_PARAM_SPARK_IMAGE instead of replaying the stock 3.5.3 file.

static_solver_export_namespace_if_unset "spark-pi"

ns="${BENCH_NAMESPACE}"
role_name="spark-pi-role"
job_name="spark-pi"
job_manifest="${STATIC_SOLVER_REPO_ROOT}/cases/spark/deploy_spark_pi/resource/spark-pi-job.yaml"
spark_image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
spark_version="${spark_image##*:}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
rendered_job_manifest="${tmp_dir}/spark-pi-job.json"

kubectl apply -f "${STATIC_SOLVER_REPO_ROOT}/cases/spark/deploy_spark_pi/resource/spark-rbac.yaml"

if ! kubectl -n "${ns}" get role "${role_name}" -o jsonpath='{.rules[0].resources}' | grep -qw 'pods'; then
  kubectl -n "${ns}" patch role "${role_name}" --type=json \
    -p='[{"op":"add","path":"/rules/0/resources/-","value":"pods"}]'
fi

kubectl -n "${ns}" delete job "${job_name}" --ignore-not-found=true
python3 - "${job_manifest}" "${rendered_job_manifest}" "${spark_image}" "${spark_version}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
spark_image = sys.argv[3]
spark_version = sys.argv[4]

payload = yaml.safe_load(source_path.read_text())
spec = ((payload.get("spec") or {}).get("template") or {}).get("spec") or {}
containers = spec.get("containers") or []
if not containers:
    raise SystemExit("spark-pi job manifest has no containers")
container = containers[0]
container["image"] = spark_image
spec["serviceAccountName"] = "spark-pi"
resources = container.setdefault("resources", {})
requests = resources.setdefault("requests", {})
requests["memory"] = "512Mi"

command = container.get("command") or []
if len(command) >= 3:
    script = command[2]
else:
    script = ""
jar_path = f"/opt/spark/examples/jars/spark-examples_2.12-{spark_version}.jar"
if jar_path not in script:
    script = """echo "=============================================="
echo "  SparkPi Job"
echo "=============================================="
export SPARK_LOCAL_IP=$(hostname -i)
/opt/spark/bin/spark-submit \\
  --master local[2] \\
  --driver-memory 512m \\
  --class org.apache.spark.examples.SparkPi \\
  {jar_path} \\
  1000
EXIT_CODE=$?
echo "SparkPi job finished with exit code: $EXIT_CODE"
exit $EXIT_CODE
""".format(jar_path=jar_path)
container["command"] = ["/bin/bash", "-c", script]

output_path.write_text(json.dumps(payload))
PY

kubectl apply -f "${rendered_job_manifest}"
kubectl -n "${ns}" wait --for=condition=complete "job/${job_name}" --timeout=300s

static_solver_write_submit "completed SparkPi job after RBAC repair"
