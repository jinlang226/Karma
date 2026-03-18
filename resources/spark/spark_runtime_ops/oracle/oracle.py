#!/usr/bin/env python3
"""
Oracle for Spark Runtime Operations Test (Native K8s Version)

Verifies that all runtime issues have been fixed using kubectl commands:
1. ConfigMap spark.executor.memory >= 512m
2. Secret api-key does not contain "EXPIRED"
3. Job spark-data-processor is not suspended and completed
4. Deployment spark-monitor is Running (rolled back to working image)
5. Job spark-batch-processor completed successfully
"""
import subprocess
import sys
import base64
import re

NAMESPACE = "spark-runtime"


def run(cmd):
    """Execute a command and return the result."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def check_spark_cluster_running():
    """Verify Spark master and workers are running."""
    # Check master
    cmd = [
        "kubectl", "get", "deployment", "spark-master",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.availableReplicas}"
    ]
    result = run(cmd)
    master_ready = result.stdout.strip() == "1"

    # Check workers
    cmd = [
        "kubectl", "get", "deployment", "spark-worker",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.availableReplicas}"
    ]
    result = run(cmd)
    workers_ready = int(result.stdout.strip() or "0") > 0

    if master_ready and workers_ready:
        print("PASS: Spark cluster is running")
        return True
    else:
        print(f"FAIL: Spark cluster not ready (master={master_ready}, workers={workers_ready})", file=sys.stderr)
        return False


def check_configmap_memory():
    """Verify ConfigMap has correct executor memory setting."""
    cmd = [
        "kubectl", "get", "configmap", "spark-config",
        "-n", NAMESPACE,
        "-o", "jsonpath={.data.spark\\.executor\\.memory}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get ConfigMap spark-config", file=sys.stderr)
        print(f"  Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    memory = result.stdout.strip()
    # Parse memory value (e.g., "512m", "1g", "1024m")
    match = re.match(r'(\d+)([mg])?', memory.lower())
    if not match:
        print(f"FAIL: Cannot parse memory value: {memory}", file=sys.stderr)
        return False

    value = int(match.group(1))
    unit = match.group(2) or 'm'

    # Convert to MB
    if unit == 'g':
        value_mb = value * 1024
    else:
        value_mb = value

    if value_mb >= 512:
        print(f"PASS: ConfigMap spark.executor.memory ({memory}) is >= 512m")
        return True
    else:
        print(f"FAIL: ConfigMap spark.executor.memory ({memory}) is < 512m", file=sys.stderr)
        return False


def check_secret_api_key():
    """Verify Secret api-key does not contain EXPIRED."""
    cmd = [
        "kubectl", "get", "secret", "spark-credentials",
        "-n", NAMESPACE,
        "-o", "jsonpath={.data.api-key}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get Secret spark-credentials", file=sys.stderr)
        print(f"  Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    encoded = result.stdout.strip()
    try:
        decoded = base64.b64decode(encoded).decode('utf-8')
    except Exception as e:
        print(f"FAIL: Cannot decode api-key: {e}", file=sys.stderr)
        return False

    if "EXPIRED" in decoded.upper():
        print(f"FAIL: Secret api-key contains 'EXPIRED': {decoded}", file=sys.stderr)
        return False

    print(f"PASS: Secret api-key is valid (does not contain 'EXPIRED')")
    return True


def check_spark_job_not_suspended():
    """Verify spark-data-processor Job is not suspended."""
    cmd = [
        "kubectl", "get", "job", "spark-data-processor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.suspend}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get Job spark-data-processor", file=sys.stderr)
        print(f"  Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    suspended = result.stdout.strip().lower()
    if suspended == "true":
        print("FAIL: Job spark-data-processor is still suspended", file=sys.stderr)
        return False

    print("PASS: Job spark-data-processor is not suspended")
    return True


def check_spark_job_completed():
    """Verify spark-data-processor Job completed."""
    cmd = [
        "kubectl", "get", "job", "spark-data-processor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get Job status", file=sys.stderr)
        return False

    succeeded = result.stdout.strip()
    if succeeded == "1":
        print(f"PASS: Job spark-data-processor completed successfully")
        return True

    # Check if still running
    cmd2 = [
        "kubectl", "get", "job", "spark-data-processor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.active}"
    ]
    result2 = run(cmd2)
    active = result2.stdout.strip()
    if active == "1":
        print(f"INFO: Job spark-data-processor is still running")
        return False

    print(f"FAIL: Job spark-data-processor not completed (succeeded={succeeded})", file=sys.stderr)
    return False


def check_deployment_running():
    """Verify Deployment spark-monitor has all pods Running."""
    cmd = [
        "kubectl", "get", "deployment", "spark-monitor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.readyReplicas}/{.spec.replicas}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get Deployment spark-monitor", file=sys.stderr)
        print(f"  Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    status = result.stdout.strip()
    parts = status.split('/')
    if len(parts) != 2:
        print(f"FAIL: Cannot parse deployment status: {status}", file=sys.stderr)
        return False

    try:
        ready = int(parts[0]) if parts[0] else 0
        desired = int(parts[1]) if parts[1] else 0
    except ValueError:
        print(f"FAIL: Cannot parse replica counts: {status}", file=sys.stderr)
        return False

    if ready > 0 and ready == desired:
        print(f"PASS: Deployment spark-monitor is Running ({ready}/{desired} replicas)")
        return True
    else:
        # Check pod status for more details
        cmd2 = [
            "kubectl", "get", "pods",
            "-n", NAMESPACE,
            "-l", "app=spark-monitor",
            "-o", "jsonpath={.items[0].status.phase}"
        ]
        result2 = run(cmd2)
        pod_status = result2.stdout.strip() if result2.returncode == 0 else "Unknown"
        print(f"FAIL: Deployment spark-monitor not ready ({ready}/{desired}), pod status: {pod_status}", file=sys.stderr)
        return False


def check_batch_job_completed():
    """Verify Job spark-batch-processor completed successfully."""
    # First check if job is still suspended
    cmd_suspend = [
        "kubectl", "get", "job", "spark-batch-processor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.suspend}"
    ]
    result_suspend = run(cmd_suspend)
    if result_suspend.returncode != 0:
        print("FAIL: Cannot get Job spark-batch-processor", file=sys.stderr)
        return False

    suspended = result_suspend.stdout.strip().lower()
    if suspended == "true":
        print("FAIL: Job spark-batch-processor is still suspended", file=sys.stderr)
        return False

    # Check job completion
    cmd = [
        "kubectl", "get", "job", "spark-batch-processor",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get Job status", file=sys.stderr)
        return False

    succeeded = result.stdout.strip()
    if succeeded == "1":
        print("PASS: Job spark-batch-processor completed successfully")
        return True
    else:
        # Check if job is still running
        cmd2 = [
            "kubectl", "get", "job", "spark-batch-processor",
            "-n", NAMESPACE,
            "-o", "jsonpath={.status.active}"
        ]
        result2 = run(cmd2)
        active = result2.stdout.strip()
        if active == "1":
            print("FAIL: Job spark-batch-processor is still running", file=sys.stderr)
        else:
            print(f"FAIL: Job spark-batch-processor not completed (succeeded={succeeded})", file=sys.stderr)
        return False


def main():
    print("=" * 60)
    print("Spark Runtime Operations - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Fixed ConfigMap spark.executor.memory >= 512m")
    print("  2. Fixed Secret api-key (removed EXPIRED)")
    print("  3. Resumed spark-data-processor Job")
    print("  4. Rolled back spark-monitor Deployment")
    print("  5. Resumed spark-batch-processor Job")
    print()

    results = {}

    # Check cluster
    print("[Spark Cluster Status]")
    results["cluster_running"] = check_spark_cluster_running()

    # Check ConfigMap
    print("\n[ConfigMap executor memory]")
    results["configmap_fixed"] = check_configmap_memory()

    # Check Secret
    print("\n[Secret api-key validity]")
    results["secret_fixed"] = check_secret_api_key()

    # Check spark-data-processor
    print("\n[spark-data-processor Job not suspended]")
    results["spark_job_not_suspended"] = check_spark_job_not_suspended()

    print("\n[spark-data-processor Job completion]")
    results["spark_job_completed"] = check_spark_job_completed()

    # Check deployment
    print("\n[spark-monitor Deployment running]")
    results["deployment_running"] = check_deployment_running()

    # Check batch job
    print("\n[spark-batch-processor Job completion]")
    results["batch_job_completed"] = check_batch_job_completed()

    # Summary
    print()
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    all_passed = all(results.values())

    print(f"\nResults:")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('cluster_running') else 'No'}")
    print(f"  - ConfigMap Fixed: {'Yes' if results.get('configmap_fixed') else 'No'}")
    print(f"  - Secret Fixed: {'Yes' if results.get('secret_fixed') else 'No'}")
    print(f"  - spark-data-processor Not Suspended: {'Yes' if results.get('spark_job_not_suspended') else 'No'}")
    print(f"  - spark-data-processor Completed: {'Yes' if results.get('spark_job_completed') else 'No'}")
    print(f"  - spark-monitor Running: {'Yes' if results.get('deployment_running') else 'No'}")
    print(f"  - spark-batch-processor Completed: {'Yes' if results.get('batch_job_completed') else 'No'}")

    print()
    print("=" * 60)

    if all_passed:
        print("SUCCESS: All runtime operations completed!")
        print("=" * 60)
        return 0
    else:
        print("INCOMPLETE: Not all issues have been fixed")
        print("=" * 60)
        print("\nThe operator needs to:")
        if not results.get("configmap_fixed"):
            print("  - Fix ConfigMap: kubectl patch configmap spark-config -n spark-runtime --type=merge -p '{\"data\":{\"spark.executor.memory\":\"512m\"}}'")
        if not results.get("secret_fixed"):
            print("  - Fix Secret: kubectl patch secret spark-credentials -n spark-runtime --type=merge -p '{\"stringData\":{\"api-key\":\"sk-valid-production-key-67890\"}}'")
        if not results.get("spark_job_not_suspended"):
            print("  - Resume spark-data-processor: kubectl patch job spark-data-processor -n spark-runtime --type=strategic -p '{\"spec\":{\"suspend\":false}}'")
        if not results.get("deployment_running"):
            print("  - Rollback spark-monitor: kubectl rollout undo deployment/spark-monitor -n spark-runtime")
        if not results.get("batch_job_completed"):
            print("  - Resume batch job: kubectl patch job spark-batch-processor -n spark-runtime --type=strategic -p '{\"spec\":{\"suspend\":false}}'")
        return 1


if __name__ == "__main__":
    sys.exit(main())
