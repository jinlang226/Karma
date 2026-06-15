#!/usr/bin/env python3
# Verify the cluster was initialized and all nodes joined. The expected node /
# pod count comes from the case param (BENCH_PARAM_REPLICA_COUNT), so a workflow
# that overrides replica_count is honored instead of a hardcoded 3. Standalone
# (default param) this behaves identically.
import argparse
import json
import os
import subprocess
import sys


REPLICA_COUNT = int(os.environ.get("BENCH_PARAM_REPLICA_COUNT", "3"))


def run(cmd, timeout=None):
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def exec_with_timeout(cmd, timeout_seconds):
    try:
        return run(cmd, timeout=timeout_seconds), None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout_seconds}s"


def parse_args():
    parser = argparse.ArgumentParser(description="Verify CockroachDB initialization.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Timeout in seconds for kubectl exec checks.",
    )
    return parser.parse_args()


def main(timeout_seconds):
    errors = []
    # Guardrail: disallow operator CRs if CRDs are installed.
    result = run(["kubectl", "-n", "cockroachdb", "get", "crdbcluster", "-o", "json"])
    if result.returncode == 0:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        if payload.get("items"):
            errors.append("CrdbCluster CRs detected; operator/CRDs are not allowed")

    # Check if cluster is initialized by running node status
    cmd = [
        "kubectl", "-n", "cockroachdb", "exec", "crdb-cluster-0", "--",
        "./cockroach", "node", "status", "--insecure"
    ]
    result, err = exec_with_timeout(cmd, timeout_seconds)
    if err:
        errors.append(f"Cluster not initialized - 'cockroach node status' {err}")
    elif result.returncode != 0:
        errors.append("Cluster not initialized - 'cockroach node status' failed")
        errors.append(f"Error: {result.stderr.strip()}")
    else:
        # Parse node status output
        lines = result.stdout.strip().split('\n')
        # Skip header lines and count data rows
        data_lines = [l for l in lines if l.strip() and not l.startswith('id') and not l.startswith('--')]
        if len(data_lines) < REPLICA_COUNT:
            errors.append(f"Expected {REPLICA_COUNT} nodes, but found {len(data_lines)}")
    
    # Test SQL connectivity
    cmd = [
        "kubectl", "-n", "cockroachdb", "exec", "crdb-cluster-0", "--",
        "./cockroach", "sql", "--insecure", "-e", "SELECT 1;"
    ]
    result, err = exec_with_timeout(cmd, timeout_seconds)
    if err:
        errors.append(f"SQL connectivity test {err}")
    elif result.returncode != 0:
        errors.append("SQL connectivity test failed")
        errors.append(f"Error: {result.stderr.strip()}")
    
    # Check all pods are running
    cmd = ["kubectl", "-n", "cockroachdb", "get", "pods", 
           "-l", "app.kubernetes.io/name=cockroachdb", "-o", "json"]
    result = run(cmd)
    if result.returncode == 0:
        try:
            pods_data = json.loads(result.stdout)
            pods = pods_data.get("items", [])
            
            for pod in pods:
                name = pod["metadata"]["name"]
                phase = pod["status"].get("phase", "Unknown")
                conditions = pod["status"].get("conditions", [])
                ready = any(c.get("type") == "Ready" and c.get("status") == "True" 
                           for c in conditions)
                
                if phase != "Running":
                    errors.append(f"Pod {name} is not Running (phase: {phase})")
                if not ready:
                    errors.append(f"Pod {name} is not Ready")
            
            if len(pods) != REPLICA_COUNT:
                errors.append(f"Expected {REPLICA_COUNT} pods, found {len(pods)}")
                
        except (json.JSONDecodeError, KeyError) as e:
            errors.append(f"Failed to parse pod status: {e}")
    
    # Print results
    if errors:
        print("Initialization verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    
    print("Cluster initialized successfully - all 3 nodes are alive and accepting SQL connections")
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(main(args.timeout))
