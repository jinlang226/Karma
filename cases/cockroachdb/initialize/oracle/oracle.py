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


_CONN_FLAG = None


def conn_flag():
    """Return the right cockroach SQL connection flag for the live cluster.

    Standalone this case runs against an INSECURE cluster (`--insecure`). But in
    a workflow this stage can inherit a SECURE cluster left running by a prior
    stage (e.g. certificate-rotation), whose precondition probe sees pods already
    Running and skips its own insecure redeploy. A hardcoded `--insecure` then
    fails with "node is running secure mode, SSL connection required". Detect the
    mode once by checking for the mounted certs dir and connect accordingly so
    the same oracle works in both contexts. Mirrors
    cockroachdb/cluster-settings/oracle/oracle.py.
    """
    global _CONN_FLAG
    if _CONN_FLAG is not None:
        return _CONN_FLAG
    probe = run([
        "kubectl", "-n", "cockroachdb", "--request-timeout=15s", "exec",
        "crdb-cluster-0", "--", "ls", "/cockroach/cockroach-certs/ca.crt",
    ])
    if probe.returncode == 0:
        _CONN_FLAG = "--certs-dir=/cockroach/cockroach-certs"
    else:
        _CONN_FLAG = "--insecure"
    return _CONN_FLAG


def exec_with_timeout(cmd, timeout_seconds):
    try:
        return run(cmd, timeout=timeout_seconds), None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout_seconds}s"


def crdb_pods():
    """Return the cluster's pod objects, robust to the build's labels (§3.1).

    Standalone this case ships its own StatefulSet with the canonical
    app.kubernetes.io/name=cockroachdb label. In a workflow this stage can
    inherit an agent-built cluster (cockroachdb/deploy) whose StatefulSet the
    deploy oracle now mandates carry the same labels -- but to be resilient we
    resolve pods by the live `crdb-cluster` StatefulSet's own selector, and fall
    back to the canonical label and then to the crdb-cluster-* name prefix.
    """
    # Prefer the live StatefulSet selector.
    sts = run(["kubectl", "-n", "cockroachdb", "get", "statefulset",
               "crdb-cluster", "-o", "json"])
    if sts.returncode == 0:
        try:
            match = (json.loads(sts.stdout).get("spec", {})
                     .get("selector", {}).get("matchLabels")) or {}
        except json.JSONDecodeError:
            match = {}
        if match:
            sel = ",".join(f"{k}={v}" for k, v in match.items())
            res = run(["kubectl", "-n", "cockroachdb", "get", "pods",
                       "-l", sel, "-o", "json"])
            if res.returncode == 0:
                try:
                    items = json.loads(res.stdout).get("items", [])
                except json.JSONDecodeError:
                    items = []
                if items:
                    return items
    # Fall back to the canonical label.
    res = run(["kubectl", "-n", "cockroachdb", "get", "pods",
               "-l", "app.kubernetes.io/name=cockroachdb", "-o", "json"])
    if res.returncode == 0:
        try:
            items = json.loads(res.stdout).get("items", [])
        except json.JSONDecodeError:
            items = []
        if items:
            return items
    # Last resort: select by the StatefulSet's stable pod-name prefix.
    res = run(["kubectl", "-n", "cockroachdb", "get", "pods", "-o", "json"])
    if res.returncode != 0:
        return []
    try:
        items = json.loads(res.stdout).get("items", [])
    except json.JSONDecodeError:
        return []
    return [p for p in items
            if p.get("metadata", {}).get("name", "").startswith("crdb-cluster-")]


def parse_args():
    parser = argparse.ArgumentParser(description="Verify CockroachDB initialization.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Timeout in seconds for kubectl exec checks.",
    )
    return parser.parse_args()


def evaluate(timeout_seconds):
    """One full snapshot of the initialization checks; returns error list."""
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
        "./cockroach", "node", "status", conn_flag()
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
        "./cockroach", "sql", conn_flag(), "-e", "SELECT 1;"
    ]
    result, err = exec_with_timeout(cmd, timeout_seconds)
    if err:
        errors.append(f"SQL connectivity test {err}")
    elif result.returncode != 0:
        errors.append("SQL connectivity test failed")
        errors.append(f"Error: {result.stderr.strip()}")
    
    # Check all pods are running (resolved robustly to the build's labels).
    try:
        pods = crdb_pods()
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
    except (KeyError, TypeError) as e:
        errors.append(f"Failed to parse pod status: {e}")

    return errors


def main(timeout_seconds):
    # Initialization brings nodes up one at a time as they join the cluster; a
    # just-started node takes a short while to become Ready and report in node
    # status. A single snapshot can race that convergence and see fewer than
    # REPLICA_COUNT nodes on a cluster healthily finishing init. Re-evaluate for
    # up to ~70s and pass on the first clean snapshot. This does not loosen the
    # check -- a node that genuinely fails to join never appears, so the oracle
    # still fails after the deadline.
    import time
    deadline = time.monotonic() + 70
    errors = evaluate(timeout_seconds)
    while errors and time.monotonic() < deadline:
        time.sleep(7)
        errors = evaluate(timeout_seconds)

    # Print results
    if errors:
        print("Initialization verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Cluster initialized successfully - all {REPLICA_COUNT} nodes are alive and accepting SQL connections")
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(main(args.timeout))
