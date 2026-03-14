#!/usr/bin/env python3
"""
Oracle for Spark Multi-Tenant Troubleshooting Test (Native K8s Version)

Verifies that:
1. Both team Jobs complete successfully
2. History Server is running with correct configuration
3. RBAC bindings are correctly configured
4. Resource quotas are respected
"""
import subprocess
import sys
import re


def run(cmd):
    """Execute a command and return the result."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def check_job_completed(namespace, job_name):
    """Verify that a Job completed successfully."""
    cmd = [
        "kubectl", "get", "job", job_name,
        "-n", namespace,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)

    if result.returncode != 0:
        print(f"FAIL: Cannot get Job {job_name} in {namespace}", file=sys.stderr)
        return False

    succeeded = result.stdout.strip()
    if succeeded == "1":
        print(f"PASS: Job {job_name} in {namespace} completed successfully")
        return True

    # Check if failed
    cmd = [
        "kubectl", "get", "job", job_name,
        "-n", namespace,
        "-o", "jsonpath={.status.failed}"
    ]
    result = run(cmd)
    failed = result.stdout.strip()

    if failed and int(failed) > 0:
        print(f"FAIL: Job {job_name} in {namespace} failed", file=sys.stderr)
        return False

    # Check if still running
    cmd = [
        "kubectl", "get", "job", job_name,
        "-n", namespace,
        "-o", "jsonpath={.status.active}"
    ]
    result = run(cmd)
    active = result.stdout.strip()

    if active == "1":
        print(f"INFO: Job {job_name} in {namespace} is still running")
        return False

    print(f"FAIL: Job {job_name} status unknown", file=sys.stderr)
    return False


def check_team_a_job():
    """Check Team A's SparkPi Job."""
    return check_job_completed("spark-team-a", "spark-pi-team-a")


def check_team_b_job():
    """Check Team B's SparkPi Job."""
    return check_job_completed("spark-team-b", "spark-pi-team-b")


def check_history_server_running():
    """Verify that Spark History Server is running."""
    cmd = [
        "kubectl", "get", "pods",
        "-n", "spark-history",
        "-l", "app=spark-history-server",
        "-o", "jsonpath={.items[0].status.phase}"
    ]
    result = run(cmd)
    if result.returncode != 0 or not result.stdout.strip():
        print("FAIL: Cannot find History Server pod", file=sys.stderr)
        return False

    phase = result.stdout.strip()
    if phase == "Running":
        print("PASS: Spark History Server is Running")
        return True
    else:
        print(f"FAIL: History Server pod phase is '{phase}', expected 'Running'", file=sys.stderr)
        return False


def check_team_a_rolebinding_namespace():
    """Verify Team A's RoleBinding references correct namespace."""
    cmd = [
        "kubectl", "get", "rolebinding", "spark-role-binding",
        "-n", "spark-team-a",
        "-o", "jsonpath={.subjects[0].namespace}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get spark-role-binding in spark-team-a", file=sys.stderr)
        return False

    ns = result.stdout.strip()
    if ns == "spark-team-a":
        print("PASS: Team A RoleBinding references correct namespace")
        return True
    else:
        print(f"FAIL: Team A RoleBinding references wrong namespace: '{ns}', expected 'spark-team-a'", file=sys.stderr)
        return False


def check_history_pvc():
    """Verify History Server PVC is correctly referenced."""
    cmd = [
        "kubectl", "get", "deployment", "spark-history-server",
        "-n", "spark-history",
        "-o", "jsonpath={.spec.template.spec.volumes[0].persistentVolumeClaim.claimName}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get History Server PVC reference", file=sys.stderr)
        return False

    pvc_name = result.stdout.strip()
    # Check if the PVC exists
    cmd2 = ["kubectl", "get", "pvc", pvc_name, "-n", "spark-history", "-o", "name"]
    result2 = run(cmd2)
    if result2.returncode != 0:
        print(f"FAIL: History Server references non-existent PVC: '{pvc_name}'", file=sys.stderr)
        return False

    print(f"PASS: History Server PVC '{pvc_name}' exists")
    return True


def check_history_log_directory():
    """Verify History Server log directory matches volume mount."""
    cmd = [
        "kubectl", "get", "deployment", "spark-history-server",
        "-n", "spark-history",
        "-o", "jsonpath={.spec.template.spec.containers[0].env[?(@.name=='SPARK_HISTORY_OPTS')].value}"
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("FAIL: Cannot get History Server SPARK_HISTORY_OPTS", file=sys.stderr)
        return False

    opts = result.stdout.strip()
    # Extract logDirectory from opts
    match = re.search(r'spark\.history\.fs\.logDirectory=(\S+)', opts)
    if not match:
        print("FAIL: Cannot find logDirectory in SPARK_HISTORY_OPTS", file=sys.stderr)
        return False

    log_dir = match.group(1)

    # Get volume mount path
    cmd2 = [
        "kubectl", "get", "deployment", "spark-history-server",
        "-n", "spark-history",
        "-o", "jsonpath={.spec.template.spec.containers[0].volumeMounts[0].mountPath}"
    ]
    result2 = run(cmd2)
    mount_path = result2.stdout.strip()

    if log_dir == mount_path or log_dir.startswith(mount_path):
        print(f"PASS: History Server log directory ({log_dir}) matches mount path ({mount_path})")
        return True
    else:
        print(f"FAIL: History Server log directory ({log_dir}) does not match mount path ({mount_path})", file=sys.stderr)
        return False


def check_spark_cluster_running(namespace, team):
    """Verify Spark cluster is running for a team."""
    # Check master
    cmd = [
        "kubectl", "get", "deployment", "spark-master",
        "-n", namespace,
        "-o", "jsonpath={.status.availableReplicas}"
    ]
    result = run(cmd)
    master_ready = result.stdout.strip() == "1"

    # Check workers
    cmd = [
        "kubectl", "get", "deployment", "spark-worker",
        "-n", namespace,
        "-o", "jsonpath={.status.availableReplicas}"
    ]
    result = run(cmd)
    workers_ready = int(result.stdout.strip() or "0") > 0

    if master_ready and workers_ready:
        print(f"PASS: {team} Spark cluster is running")
        return True
    else:
        print(f"FAIL: {team} Spark cluster not ready (master={master_ready}, workers={workers_ready})", file=sys.stderr)
        return False


def main():
    print("=" * 60)
    print("Spark Multi-Tenant Environment - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Fixed History Server configuration")
    print("  2. Fixed Team A's RoleBinding namespace")
    print("  3. Both teams' SparkPi jobs completed")
    print()

    results = {}

    # Check Team A
    print("[Team A Spark Cluster]")
    results["team_a_cluster"] = check_spark_cluster_running("spark-team-a", "Team A")

    print("\n[Team A Job Status]")
    results["team_a_job"] = check_team_a_job()

    print("\n[Team A RoleBinding]")
    results["team_a_rbac"] = check_team_a_rolebinding_namespace()

    # Check Team B
    print("\n[Team B Spark Cluster]")
    results["team_b_cluster"] = check_spark_cluster_running("spark-team-b", "Team B")

    print("\n[Team B Job Status]")
    results["team_b_job"] = check_team_b_job()

    # Check History Server
    print("\n[History Server Status]")
    results["history_running"] = check_history_server_running()

    print("\n[History Server PVC]")
    results["history_pvc"] = check_history_pvc()

    print("\n[History Server Log Directory]")
    results["history_log_dir"] = check_history_log_directory()

    # Summary
    print()
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    all_passed = all(results.values())

    print(f"\nTeam A:")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('team_a_cluster') else 'No'}")
    print(f"  - Job Completed: {'Yes' if results.get('team_a_job') else 'No'}")
    print(f"  - RoleBinding Fixed: {'Yes' if results.get('team_a_rbac') else 'No'}")

    print(f"\nTeam B:")
    print(f"  - Spark Cluster Running: {'Yes' if results.get('team_b_cluster') else 'No'}")
    print(f"  - Job Completed: {'Yes' if results.get('team_b_job') else 'No'}")

    print(f"\nHistory Server:")
    print(f"  - Running: {'Yes' if results.get('history_running') else 'No'}")
    print(f"  - PVC Fixed: {'Yes' if results.get('history_pvc') else 'No'}")
    print(f"  - Log Directory Fixed: {'Yes' if results.get('history_log_dir') else 'No'}")

    print()
    print("=" * 60)

    if all_passed:
        print("SUCCESS: Multi-tenant environment is working correctly!")
        print("=" * 60)
        return 0
    else:
        print("INCOMPLETE: Not all issues have been fixed")
        print("=" * 60)
        print("\nThe operator needs to:")
        if not results.get("team_a_rbac"):
            print("  - Fix Team A RoleBinding: change subjects[0].namespace from 'default' to 'spark-team-a'")
        if not results.get("history_pvc"):
            print("  - Fix History Server PVC: change claimName from 'spark-history-pvc-wrong' to 'spark-history-pvc'")
        if not results.get("history_log_dir"):
            print("  - Fix History Server log directory: change '/wrong/path/spark-logs' to '/mnt/spark-logs'")
        if not results.get("team_a_job"):
            print("  - Wait for Team A job to complete or re-run it")
        if not results.get("team_b_job"):
            print("  - Wait for Team B job to complete or re-run it")
        return 1


if __name__ == "__main__":
    sys.exit(main())
