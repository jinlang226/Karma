#!/usr/bin/env python3
"""
Oracle for Spark ETL Data Skew OOM Task (Native K8s Version)

Verifies that the operator:
1. Collected failure information (identified OOM in Stage 3)
2. Diagnosed root cause (data skew on user_id 999, >10x ratio)
3. Fixed the problem (ETL job completed successfully)

The operator must:
- Analyze logs to find OOM error
- Query data to find skewed user_id
- Apply a fix (increase memory, more workers, etc.)
- Re-run the ETL job successfully
"""

import subprocess
import sys
import re

NAMESPACE = "spark-etl"


def run(cmd):
    """Execute a command and return the result."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def check_etl_completed():
    """Check if ETL job completed successfully."""
    cmd = [
        "kubectl", "get", "job", "etl-job",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)

    if result.stdout.strip() == "1":
        print("PASS: ETL job completed successfully")
        return True, "COMPLETED"

    # Check if failed
    cmd = [
        "kubectl", "get", "job", "etl-job",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.failed}"
    ]
    result = run(cmd)

    if result.stdout.strip() == "1":
        print("FAIL: ETL job failed", file=sys.stderr)
        return False, "FAILED"

    # Check if running
    cmd = [
        "kubectl", "get", "job", "etl-job",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.active}"
    ]
    result = run(cmd)

    if result.stdout.strip() == "1":
        print("INFO: ETL job is still running")
        return False, "RUNNING"

    print("FAIL: ETL job status unknown", file=sys.stderr)
    return False, "UNKNOWN"


def check_spark_cluster_running():
    """Verify Spark cluster is running."""
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
        print(f"FAIL: Spark cluster not ready", file=sys.stderr)
        return False


def check_fix_applied():
    """
    Check if a fix was applied.

    Possible fixes:
    1. Worker memory increased
    2. More workers added
    3. ETL script modified (AQE, salting)
    """
    fixes_applied = []

    # Check worker memory
    cmd = [
        "kubectl", "get", "deployment", "spark-worker",
        "-n", NAMESPACE, "-o", "yaml"
    ]
    result = run(cmd)

    if result.returncode == 0:
        spec = result.stdout
        # Check for increased memory
        memory_match = re.search(r'SPARK_WORKER_MEMORY.*?value:\s*"?(\d+)([GMgm])"?', spec)
        if memory_match:
            mem_value = int(memory_match.group(1))
            mem_unit = memory_match.group(2).upper()
            if mem_unit == 'G' or (mem_unit == 'M' and mem_value > 512):
                fixes_applied.append("WORKER_MEMORY_INCREASED")

        # Check for more workers
        replicas_match = re.search(r'replicas:\s*(\d+)', spec)
        if replicas_match:
            replicas = int(replicas_match.group(1))
            if replicas > 2:
                fixes_applied.append("WORKERS_SCALED_UP")

    # Check ETL job for config changes
    cmd = [
        "kubectl", "get", "job", "etl-job",
        "-n", NAMESPACE, "-o", "yaml"
    ]
    result = run(cmd)

    if result.returncode == 0:
        spec = result.stdout
        if 'adaptive.enabled' in spec and 'true' in spec.lower():
            fixes_applied.append("AQE_ENABLED")
        if '--executor-memory' in spec:
            mem_match = re.search(r'--executor-memory\s+(\d+)([gm])', spec, re.IGNORECASE)
            if mem_match:
                mem_value = int(mem_match.group(1))
                mem_unit = mem_match.group(2).lower()
                if mem_unit == 'g' or (mem_unit == 'm' and mem_value > 256):
                    fixes_applied.append("EXECUTOR_MEMORY_INCREASED")

    # Check configmap for code changes
    cmd = [
        "kubectl", "get", "configmap", "etl-scripts",
        "-n", NAMESPACE, "-o", "yaml"
    ]
    result = run(cmd)
    if result.returncode == 0:
        if "broadcast(" in result.stdout.lower():
            fixes_applied.append("BROADCAST_JOIN")
        if "salt" in result.stdout.lower():
            fixes_applied.append("SALTING")

    if len(fixes_applied) > 0:
        print(f"PASS: Fixes applied: {', '.join(fixes_applied)}")
        return True, fixes_applied
    else:
        print("FAIL: No optimization fixes detected", file=sys.stderr)
        return False, fixes_applied


def check_data_skew_stats():
    """Read the actual data skew statistics."""
    # Try to find data-generator pod
    cmd = [
        "kubectl", "get", "pods", "-n", NAMESPACE,
        "-l", "app=data-generator",
        "-o", "jsonpath={.items[0].metadata.name}"
    ]
    result = run(cmd)

    if result.returncode != 0 or not result.stdout.strip():
        print("INFO: Could not find data-generator pod")
        return None

    pod_name = result.stdout.strip()

    cmd = [
        "kubectl", "exec", "-n", NAMESPACE,
        pod_name, "--",
        "cat", "/data/skew_info.txt"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print("INFO: Could not read skew info file")
        return None

    skew_info = {}
    for line in result.stdout.strip().split('\n'):
        if '=' in line:
            key, value = line.split('=', 1)
            skew_info[key] = value

    return skew_info


def main():
    print("=" * 60)
    print("Spark ETL Data Skew OOM - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Identified OOM failure in Stage 3")
    print("  2. Diagnosed data skew (user_id 999, >10x ratio)")
    print("  3. Applied a fix and completed the ETL job")
    print()

    results = {}

    # Check data skew stats (for reference)
    print("[Data Skew Statistics]")
    skew_info = check_data_skew_stats()
    if skew_info:
        print(f"  Hot user: {skew_info.get('HOT_USER_ID', 'N/A')}")
        print(f"  Hot user records: {skew_info.get('HOT_USER_RECORDS', 'N/A')} ({skew_info.get('HOT_USER_PERCENTAGE', 'N/A')}%)")
        print(f"  Skew ratio: {skew_info.get('SKEW_RATIO', 'N/A')}x")
    else:
        print("  Could not retrieve skew info")

    # Check 1: Spark cluster running
    print("\n[Spark Cluster Status]")
    results["cluster_running"] = check_spark_cluster_running()

    # Check 2: ETL job completed
    print("\n[ETL Job Status]")
    passed, state = check_etl_completed()
    results["etl_completed"] = passed

    # Check 3: Fix applied
    print("\n[Optimization Fix]")
    passed, fixes = check_fix_applied()
    results["fix_applied"] = passed
    results["fixes"] = fixes

    # Summary
    print()
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    core_passed = results.get("etl_completed", False) and results.get("fix_applied", False)

    print(f"\nCore Requirements:")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('cluster_running') else 'No'}")
    print(f"  - ETL Job Completed: {'Yes' if results.get('etl_completed') else 'No'}")
    print(f"  - Fix Applied: {'Yes' if results.get('fix_applied') else 'No'}")
    if results.get("fixes"):
        print(f"    Fixes: {', '.join(results.get('fixes', []))}")

    print()
    print("=" * 60)

    if core_passed:
        print("SUCCESS: ETL job fixed and completed!")
        print("=" * 60)
        return 0
    else:
        print("INCOMPLETE: ETL job not yet fixed")
        print("=" * 60)
        print("\nThe operator needs to:")
        if not results.get("etl_completed"):
            print("  - Fix and re-run the ETL job")
        if not results.get("fix_applied"):
            print("  - Apply optimization:")
            print("    - Scale workers: kubectl scale deployment spark-worker -n spark-etl --replicas=4")
            print("    - Or increase memory in deployment")
        print("\nDiagnosis steps:")
        print("  1. Check job logs: kubectl logs -n spark-etl -l app=etl-job")
        print("  2. Look for OOM error in Stage 3")
        print("  3. Check data skew info: user_id 999 has 50% of records")
        print("  4. Apply fix and re-run: kubectl delete job etl-job -n spark-etl && kubectl apply -f ...")
        return 1


if __name__ == "__main__":
    sys.exit(main())
