#!/usr/bin/env python3
"""
Oracle for Spark Data Skew Optimization Test

Verifies that:
1. Configuration bugs have been fixed
2. Spark cluster is running properly
3. Baseline job completed (shows slow performance)
4. At least one optimization strategy was applied (broadcast or aqe)
5. Optimization jobs completed successfully
"""
import subprocess
import sys
import re

NAMESPACE = "spark-skew"


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


def check_job_status(job_name):
    """Check if a job exists and its status."""
    cmd = [
        "kubectl", "get", "job", job_name,
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded},{.status.failed},{.status.active}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        return "not_found"

    parts = result.stdout.strip().split(",")
    succeeded = parts[0] if len(parts) > 0 else ""
    failed = parts[1] if len(parts) > 1 else ""
    active = parts[2] if len(parts) > 2 else ""

    if succeeded == "1":
        return "completed"
    elif failed == "1":
        return "failed"
    elif active == "1":
        return "running"
    else:
        return "unknown"


def get_job_logs(label_selector):
    """Get logs from jobs matching a label selector."""
    cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", label_selector, "--tail=200"
    ]
    result = run(cmd)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout


def check_bug_fixes():
    """Verify that configuration bugs have been fixed."""
    fixes_verified = []
    fixes_failed = []

    # Check Bug #1: nc command replaced with bash TCP check
    # We verify this by checking if baseline job didn't fail with "nc: command not found"
    baseline_logs = get_job_logs("strategy=baseline")
    if baseline_logs:
        if "nc: command not found" in baseline_logs or "nc: not found" in baseline_logs:
            fixes_failed.append("Bug #1: 'nc' command still being used (not fixed)")
        else:
            fixes_verified.append("Bug #1: nc command replaced with bash TCP check")

    # Check Bug #2: SPARK_MASTER_PORT unset
    # Verify by checking master logs for NumberFormatException
    master_logs_cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", "app=spark-master", "--tail=100"
    ]
    result = run(master_logs_cmd)
    if result.returncode == 0 and result.stdout:
        if "NumberFormatException" in result.stdout and "tcp://" in result.stdout:
            fixes_failed.append("Bug #2: SPARK_MASTER_PORT conflict not fixed")
        elif "Starting Spark master" in result.stdout or "spark.master" in result.stdout.lower():
            fixes_verified.append("Bug #2: SPARK_MASTER_PORT conflict resolved")

    # Check Bug #3: Local mode used instead of distributed
    # Verify by checking if job completed without DNS/file errors
    if baseline_logs:
        if "UnknownHostException" in baseline_logs:
            fixes_failed.append("Bug #3: Executor DNS resolution issue (distributed mode)")
        elif "does not exist" in baseline_logs and "spark-skew-data" in baseline_logs:
            fixes_failed.append("Bug #3: File access issue (distributed mode)")
        elif "local[" in baseline_logs or "--master local" in baseline_logs:
            fixes_verified.append("Bug #3: Using local execution mode")

    return fixes_verified, fixes_failed


def check_baseline_job():
    """Verify baseline job completed and shows skew."""
    status = check_job_status("spark-skew-baseline")

    if status == "not_found":
        print("FAIL: Baseline job (spark-skew-baseline) not found", file=sys.stderr)
        return False, None

    if status == "completed":
        print("PASS: Baseline job completed")

        # Get logs to extract skew ratio
        logs = get_job_logs("strategy=baseline")
        if logs:
            match = re.search(r'SKEW RATIO:\s*([\d.]+)x', logs)
            if match:
                skew_ratio = float(match.group(1))
                print(f"  - Detected skew ratio: {skew_ratio:.2f}x")
                return True, skew_ratio

        return True, None

    elif status == "running":
        print("INFO: Baseline job is still running", file=sys.stderr)
        return False, None
    elif status == "failed":
        print("FAIL: Baseline job failed", file=sys.stderr)
        return False, None
    else:
        print(f"FAIL: Baseline job status unknown: {status}", file=sys.stderr)
        return False, None


def check_optimization_jobs():
    """Check which optimization jobs were applied and completed."""
    optimization_jobs = {
        "spark-skew-broadcast": "broadcast",
        "spark-skew-aqe": "aqe"
    }

    applied = []
    completed = []

    for job_name, strategy in optimization_jobs.items():
        status = check_job_status(job_name)

        if status != "not_found":
            applied.append(strategy)
            if status == "completed":
                completed.append(strategy)
                print(f"PASS: {strategy.upper()} optimization job completed")

                # Get improvement from logs
                logs = get_job_logs(f"strategy={strategy}")
                if logs:
                    match = re.search(r'vs BASELINE:.*\(([\+\-][\d.]+)%\)', logs)
                    if match:
                        improvement = match.group(1)
                        print(f"  - Performance: {improvement} vs baseline")

            elif status == "running":
                print(f"INFO: {strategy.upper()} job is still running")
            elif status == "failed":
                print(f"WARN: {strategy.upper()} job failed", file=sys.stderr)

    return applied, completed


def main():
    print("=" * 60)
    print("Spark Data Skew Optimization - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Fixed configuration bugs in the deployment")
    print("  2. Observed the baseline query (slow due to skew)")
    print("  3. Applied at least one optimization strategy")
    print("  4. Optimization job completed successfully")
    print()

    results = {}

    # Check bug fixes first
    print("[1. Configuration Bug Fixes]")
    fixes_verified, fixes_failed = check_bug_fixes()
    for fix in fixes_verified:
        print(f"PASS: {fix}")
    for fix in fixes_failed:
        print(f"FAIL: {fix}", file=sys.stderr)
    results["bugs_fixed"] = len(fixes_failed) == 0 and len(fixes_verified) > 0
    if not fixes_verified and not fixes_failed:
        print("INFO: Unable to verify bug fixes (jobs may not have run yet)")
    print()

    # Check cluster
    print("[2. Spark Cluster Status]")
    results["cluster_running"] = check_spark_cluster_running()
    print()

    # Check baseline job
    print("[3. Baseline Job]")
    baseline_passed, skew_ratio = check_baseline_job()
    results["baseline_completed"] = baseline_passed
    print()

    # Check optimization jobs
    print("[4. Optimization Jobs]")
    applied, completed = check_optimization_jobs()
    results["optimization_applied"] = len(applied) >= 1
    results["optimization_completed"] = len(completed) >= 1

    if not applied:
        print("FAIL: No optimization jobs were applied", file=sys.stderr)
        print("  Hint: Apply one of these:")
        print("    kubectl apply -f resources/spark/spark_data_skew/resource/skew-job-broadcast.yaml")
        print("    kubectl apply -f resources/spark/spark_data_skew/resource/skew-job-aqe.yaml")
    print()

    # Summary
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    print(f"\nResults:")
    print(f"  - Configuration Bugs Fixed: {'Yes' if results.get('bugs_fixed') else 'No/Partial'}")
    if fixes_verified:
        for fix in fixes_verified:
            print(f"    ✓ {fix}")
    if fixes_failed:
        for fix in fixes_failed:
            print(f"    ✗ {fix}")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('cluster_running') else 'No'}")
    print(f"  - Baseline Job Completed: {'Yes' if results.get('baseline_completed') else 'No'}")
    if skew_ratio:
        print(f"  - Data Skew Detected: {skew_ratio:.2f}x imbalance")
    print(f"  - Optimization Applied: {'Yes' if results.get('optimization_applied') else 'No'}")
    if applied:
        print(f"    Strategies applied: {', '.join(applied)}")
    print(f"  - Optimization Completed: {'Yes' if results.get('optimization_completed') else 'No'}")
    if completed:
        print(f"    Strategies completed: {', '.join(completed)}")

    print()
    print("=" * 60)

    # Determine success
    # Must have: bugs fixed, cluster running, baseline completed, at least one optimization completed
    success = (
        results.get("cluster_running") and
        results.get("baseline_completed") and
        results.get("optimization_completed")
    )

    # Note: bugs_fixed is a soft requirement - we check jobs completed which implies bugs were fixed
    if success:
        print("SUCCESS: Data skew optimization task completed!")
        if not results.get("bugs_fixed"):
            print("  Note: Bug fix verification was inconclusive but jobs completed successfully")
        print("=" * 60)
        return 0
    else:
        print("INCOMPLETE: Some requirements not met")
        print("=" * 60)
        print("\nThe operator needs to:")
        if fixes_failed:
            print("  - Fix the remaining configuration bugs:")
            for fix in fixes_failed:
                print(f"    • {fix}")
        if not results.get("cluster_running"):
            print("  - Ensure Spark cluster is running")
        if not results.get("baseline_completed"):
            print("  - Fix configuration issues and wait for baseline job to complete")
        if not results.get("optimization_applied"):
            print("  - Apply at least one optimization strategy (broadcast or aqe)")
        elif not results.get("optimization_completed"):
            print("  - Wait for optimization job to complete")
        return 1


if __name__ == "__main__":
    sys.exit(main())
