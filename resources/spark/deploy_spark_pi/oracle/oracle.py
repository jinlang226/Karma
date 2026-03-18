#!/usr/bin/env python3
"""
Oracle for Spark Pi Troubleshooting Test (Native K8s Version)

Verifies that:
1. Spark cluster is running
2. SparkPi Job completes successfully
3. Pi calculation result is present in logs
4. Configuration fixes were applied correctly
"""
import subprocess
import sys
import re

NAMESPACE = "spark-pi"


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


def check_job_completed():
    """Verify that the SparkPi Job completed successfully."""
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("FAIL: Cannot get Job status", file=sys.stderr)
        print(f"  Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    succeeded = result.stdout.strip()
    if succeeded == "1":
        print("PASS: SparkPi Job completed successfully")
        return True

    # Check if failed
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.failed}"
    ]
    result = run(cmd)
    failed = result.stdout.strip()

    if failed == "1":
        print("FAIL: SparkPi Job failed", file=sys.stderr)
        print("  Hint: Check 'kubectl logs -n spark-pi -l app=spark-pi' for failure reason", file=sys.stderr)
        return False

    # Check if still running
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.active}"
    ]
    result = run(cmd)
    active = result.stdout.strip()

    if active == "1":
        print("INFO: SparkPi Job is still running")
        return False

    print("FAIL: SparkPi Job status unknown", file=sys.stderr)
    return False


def check_pi_result_in_logs():
    """Verify that the logs contain the Pi calculation result."""
    # Get job pod logs
    cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", "app=spark-pi", "--tail=100"
    ]
    result = run(cmd)

    if result.returncode != 0 or not result.stdout.strip():
        print("FAIL: Cannot get logs from SparkPi job", file=sys.stderr)
        return False

    # Search for Pi result
    pi_pattern = r"Pi is roughly ([\d.]+)"
    match = re.search(pi_pattern, result.stdout)

    if not match:
        print("FAIL: Pi calculation result not found in logs", file=sys.stderr)
        return False

    pi_value = float(match.group(1))
    if 3.0 <= pi_value <= 3.3:
        print(f"PASS: Pi calculation result found: {pi_value}")
        return True
    else:
        print(f"FAIL: Pi value {pi_value} is outside expected range [3.0, 3.3]", file=sys.stderr)
        return False


def check_image_fixed():
    """Verify that the image was corrected to a valid image."""
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.template.spec.containers[0].image}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("FAIL: Cannot get Job image", file=sys.stderr)
        return False

    image = result.stdout.strip()
    # Check it's not the broken image
    if "nonexistent" in image:
        print(f"FAIL: Image still contains broken value: {image}", file=sys.stderr)
        return False

    if "apache/spark" in image or "spark:" in image:
        print(f"PASS: Image configuration is valid: {image}")
        return True

    print(f"PASS: Image was changed to: {image}")
    return True


def check_serviceaccount_fixed():
    """Verify that the ServiceAccount was corrected."""
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.template.spec.serviceAccountName}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("FAIL: Cannot get serviceAccountName", file=sys.stderr)
        return False

    sa = result.stdout.strip()
    if sa == "spark-nonexistent":
        print(f"FAIL: ServiceAccount still has broken value: {sa}", file=sys.stderr)
        return False

    # Verify the SA exists
    cmd = ["kubectl", "get", "serviceaccount", sa, "-n", NAMESPACE, "-o", "name"]
    result = run(cmd)
    if result.returncode != 0:
        print(f"FAIL: ServiceAccount '{sa}' does not exist", file=sys.stderr)
        return False

    print(f"PASS: ServiceAccount configuration is valid: {sa}")
    return True


def check_memory_format_fixed():
    """Verify that the memory format was corrected."""
    cmd = [
        "kubectl", "get", "job", "spark-pi",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.template.spec.containers[0].resources.requests.memory}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("FAIL: Cannot get memory configuration", file=sys.stderr)
        return False

    memory = result.stdout.strip()
    # Check it has a unit (Mi, Gi, m, g, etc.)
    if memory and re.match(r'^\d+[mMgGkK]i?$', memory):
        print(f"PASS: Memory format is valid: {memory}")
        return True
    elif memory == "512":
        print(f"FAIL: Memory still missing unit: {memory}", file=sys.stderr)
        return False
    else:
        print(f"PASS: Memory configuration: {memory}")
        return True


def check_rbac_has_pods_permission():
    """Verify that RBAC includes pods permission."""
    cmd = [
        "kubectl", "get", "role", "spark-pi-role",
        "-n", NAMESPACE,
        "-o", "jsonpath={.rules[*].resources}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("FAIL: Cannot get spark-pi-role permissions", file=sys.stderr)
        return False

    resources = result.stdout.strip()
    if "pods" in resources:
        print("PASS: RBAC includes 'pods' permission")
        return True
    else:
        print(f"FAIL: RBAC missing 'pods' permission. Current resources: {resources}", file=sys.stderr)
        return False


def main():
    print("=" * 60)
    print("Spark Pi Troubleshooting - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Fixed image name (removed -nonexistent)")
    print("  2. Fixed serviceAccountName (spark-pi)")
    print("  3. Fixed memory format (added unit)")
    print("  4. Fixed RBAC permissions (added pods)")
    print("  5. SparkPi Job completed successfully")
    print()

    results = {}

    # Check cluster
    print("[Spark Cluster Status]")
    results["cluster_running"] = check_spark_cluster_running()

    # Core functionality checks
    print("\n[Core Functionality]")
    results["job_completed"] = check_job_completed()
    results["pi_result"] = check_pi_result_in_logs()

    # Configuration fix checks
    print("\n[Configuration Fixes]")
    results["image_fixed"] = check_image_fixed()
    results["sa_fixed"] = check_serviceaccount_fixed()
    results["memory_fixed"] = check_memory_format_fixed()
    results["rbac_fixed"] = check_rbac_has_pods_permission()

    # Summary
    print()
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    core_passed = results.get("job_completed", False) and results.get("pi_result", False)
    config_fixed = (
        results.get("image_fixed", False) and
        results.get("sa_fixed", False) and
        results.get("memory_fixed", False) and
        results.get("rbac_fixed", False)
    )

    print(f"\nCore Requirements:")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('cluster_running') else 'No'}")
    print(f"  - Job Completed: {'Yes' if results.get('job_completed') else 'No'}")
    print(f"  - Pi Result Found: {'Yes' if results.get('pi_result') else 'No'}")

    print(f"\nConfiguration Fixes:")
    print(f"  - Image Fixed: {'Yes' if results.get('image_fixed') else 'No'}")
    print(f"  - ServiceAccount Fixed: {'Yes' if results.get('sa_fixed') else 'No'}")
    print(f"  - Memory Format Fixed: {'Yes' if results.get('memory_fixed') else 'No'}")
    print(f"  - RBAC Pods Permission: {'Yes' if results.get('rbac_fixed') else 'No'}")

    print()
    print("=" * 60)

    if core_passed and config_fixed:
        print("SUCCESS: All troubleshooting tasks completed!")
        print("=" * 60)
        return 0
    else:
        print("INCOMPLETE: Not all issues have been fixed")
        print("=" * 60)
        print("\nThe operator needs to:")
        if not results.get("image_fixed"):
            print("  - Fix image: change 'apache/spark:3.5.3-nonexistent' to 'apache/spark:3.5.3'")
        if not results.get("sa_fixed"):
            print("  - Fix serviceAccountName: change 'spark-nonexistent' to 'spark-pi'")
        if not results.get("memory_fixed"):
            print("  - Fix memory: add unit (e.g., '512' -> '512Mi')")
        if not results.get("rbac_fixed"):
            print("  - Fix RBAC: add 'pods' to resources in Role")
        if not results.get("job_completed"):
            print("  - Re-run the job after fixes: kubectl delete job spark-pi -n spark-pi && kubectl apply -f ...")
        return 1


if __name__ == "__main__":
    sys.exit(main())
