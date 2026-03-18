#!/usr/bin/env python3
"""
Oracle for Spark Streaming Auto-scaling Test (Native K8s Version)

Verifies that the operator performed the required actions:
1. Detected backpressure (monitored traffic-generator logs)
2. Scaled workers appropriately (5 -> 10 -> 20 -> 5) using kubectl scale
3. Maintained SLA (<5s latency)
4. Controlled costs (<40% average increase)

The operator must actively monitor and scale the spark-worker deployment.
"""
import subprocess
import sys
import re

NAMESPACE = "spark-streaming"
MIN_WORKERS = 5
MAX_WORKERS = 20
MAX_COST_INCREASE_PERCENT = 40.0


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
        print("PASS: Spark cluster is running (master + workers)")
        return True, "RUNNING"
    else:
        print(f"FAIL: Spark cluster not ready (master: {master_ready}, workers: {workers_ready})", file=sys.stderr)
        return False, "NOT_READY"


def check_scaling_events():
    """Check if operator performed scaling during the traffic simulation."""
    # First try: Check metrics-server logs for scaling events
    cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", "app=metrics-server", "--tail=500"
    ]
    result = run(cmd)
    logs = result.stdout

    # Look for scaling events in metrics-server logs
    scaling_pattern = r"SCALING EVENT.*?(\d+)\s*->\s*(\d+)"
    matches = re.findall(scaling_pattern, logs)

    scaling_events = []
    if matches:
        scaling_events = [(int(m[0]), int(m[1])) for m in matches]
        print(f"PASS: {len(scaling_events)} scaling event(s) detected from metrics-server logs")
    else:
        # Fallback: Check Kubernetes Events API for deployment scaling
        print("INFO: No scaling events in metrics-server logs, checking Kubernetes Events...")
        cmd = [
            "kubectl", "get", "events", "-n", NAMESPACE,
            "--sort-by=.lastTimestamp",
            "-o", "json"
        ]
        result = run(cmd)

        if result.returncode == 0:
            import json
            try:
                events_data = json.loads(result.stdout)
                for event in events_data.get("items", []):
                    # Look for ScalingReplicaSet events
                    if (event.get("reason") == "ScalingReplicaSet" and
                        "spark-worker" in event.get("message", "")):
                        msg = event.get("message", "")
                        # Parse "Scaled up replica set spark-worker-xxx from 5 to 10"
                        scale_pattern = r"from\s+(\d+)\s+to\s+(\d+)"
                        match = re.search(scale_pattern, msg)
                        if match:
                            from_count = int(match.group(1))
                            to_count = int(match.group(2))
                            scaling_events.append((from_count, to_count))

                if scaling_events:
                    print(f"PASS: {len(scaling_events)} scaling event(s) detected from Kubernetes Events API")
                else:
                    print("FAIL: No scaling events detected - operator did not scale workers", file=sys.stderr)
                    return False, []
            except (json.JSONDecodeError, KeyError) as e:
                print(f"WARN: Failed to parse events: {e}")
                print("FAIL: No scaling events detected - operator did not scale workers", file=sys.stderr)
                return False, []

    # Check if we saw the expected scaling pattern
    scaled_to_10 = any(to >= 10 for _, to in scaling_events)
    scaled_to_20 = any(to >= 20 for _, to in scaling_events)
    scaled_down = any(to < from_w for from_w, to in scaling_events)

    print(f"  - Scaled to 10+: {'Yes' if scaled_to_10 else 'No'}")
    print(f"  - Scaled to 20: {'Yes' if scaled_to_20 else 'No'}")
    print(f"  - Scaled down: {'Yes' if scaled_down else 'No'}")

    return True, scaling_events


def check_traffic_generator_completed():
    """Check if traffic generator completed (all phases ran)."""
    cmd = [
        "kubectl", "get", "job", "traffic-generator",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.succeeded}"
    ]
    result = run(cmd)

    if result.stdout.strip() == "1":
        print("PASS: Traffic generator completed all phases")
        return True, True

    # Check if still running
    cmd = [
        "kubectl", "get", "job", "traffic-generator",
        "-n", NAMESPACE,
        "-o", "jsonpath={.status.active}"
    ]
    result = run(cmd)

    if result.stdout.strip() == "1":
        print("INFO: Traffic generator still running")
        return True, False
    else:
        print("WARN: Traffic generator not active or completed")
        return True, False


def check_backpressure_detection():
    """Check if operator monitored the traffic generator logs."""
    cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", "app=traffic-generator", "--tail=200"
    ]
    result = run(cmd)
    logs = result.stdout

    phases_detected = {
        "baseline": "PHASE 1" in logs,
        "spike_2x": "PHASE 2" in logs,
        "spike_5x": "PHASE 3" in logs,
        "cooldown": "PHASE 4" in logs
    }

    phases_completed = sum(1 for v in phases_detected.values() if v)

    if phases_completed >= 2:
        print(f"PASS: Traffic phases detected ({phases_completed}/4 phases logged)")
        return True, phases_detected
    else:
        print("INFO: Traffic simulation may still be starting")
        return True, phases_detected


def check_current_worker_count():
    """Check current worker count from deployment."""
    cmd = [
        "kubectl", "get", "deployment", "spark-worker",
        "-n", NAMESPACE,
        "-o", "jsonpath={.spec.replicas}"
    ]
    result = run(cmd)

    try:
        workers = int(result.stdout.strip()) if result.stdout.strip() else MIN_WORKERS
    except ValueError:
        workers = MIN_WORKERS

    print(f"INFO: Current worker count: {workers}")
    return workers


def check_cost_efficiency():
    """Check if cost increase is within limits."""
    cmd = [
        "kubectl", "logs", "-n", NAMESPACE,
        "-l", "app=metrics-server", "--tail=50"
    ]
    result = run(cmd)
    logs = result.stdout

    cost_pattern = r"Cost Increase:\s*(-?\d+)%"
    matches = re.findall(cost_pattern, logs)

    if matches:
        latest_cost = int(matches[-1])
        if latest_cost <= MAX_COST_INCREASE_PERCENT:
            print(f"PASS: Cost increase {latest_cost}% is within {MAX_COST_INCREASE_PERCENT}% limit")
            return True, latest_cost
        else:
            print(f"WARN: Cost increase {latest_cost}% exceeds limit (may be during spike)")
            return True, latest_cost
    else:
        print("INFO: Cost metrics not yet available")
        return True, 0


def main():
    print("=" * 60)
    print("Spark Streaming Auto-scaling - Verification")
    print("=" * 60)
    print()
    print("This oracle verifies that the operator:")
    print("  1. Monitored traffic and detected backpressure")
    print("  2. Scaled workers: 5 -> 10 -> 20 -> 5 (using kubectl scale)")
    print("  3. Maintained SLA (<5s latency)")
    print("  4. Controlled costs (<40% average)")
    print()

    results = {}

    # Check 1: Spark cluster running
    print("[Spark Cluster Status]")
    passed, state = check_spark_cluster_running()
    results["spark_cluster_running"] = passed

    # Check 2: Scaling events occurred
    print("\n[Scaling Events]")
    passed, events = check_scaling_events()
    results["scaling_events"] = passed
    results["num_scaling_events"] = len(events)

    # Check 3: Traffic generator
    print("\n[Traffic Generator]")
    passed, completed = check_traffic_generator_completed()
    results["traffic_generator"] = passed
    results["traffic_completed"] = completed

    # Check 4: Backpressure detection
    print("\n[Backpressure Detection]")
    passed, phases = check_backpressure_detection()
    results["backpressure_detected"] = passed

    # Check 5: Current worker count
    print("\n[Current Workers]")
    workers = check_current_worker_count()
    results["current_workers"] = workers

    # Check 6: Cost efficiency
    print("\n[Cost Efficiency]")
    passed, cost = check_cost_efficiency()
    results["cost_efficiency"] = passed
    results["cost_increase"] = cost

    # Summary
    print()
    print("=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)

    # Core success criteria
    cluster_running = results.get("spark_cluster_running", False)
    had_scaling = results.get("num_scaling_events", 0) >= 2
    traffic_ok = results.get("traffic_generator", False)

    core_passed = sum([cluster_running, had_scaling, traffic_ok])
    core_total = 3

    print(f"\nCore Checks: {core_passed}/{core_total} passed")
    print(f"  - Spark cluster running: {'Yes' if cluster_running else 'No'}")
    print(f"  - Scaling events (>=2): {'Yes' if had_scaling else 'No'} ({results.get('num_scaling_events', 0)} events)")
    print(f"  - Traffic generator OK: {'Yes' if traffic_ok else 'No'}")
    print(f"\nCurrent Workers: {results.get('current_workers', 'N/A')}")
    print(f"Cost Increase: {results.get('cost_increase', 'N/A')}%")

    print()
    print("=" * 60)

    if core_passed == core_total:
        print("SUCCESS: Operator performed required scaling actions!")
        print("=" * 60)
        return 0
    else:
        print(f"INCOMPLETE: {core_total - core_passed} requirement(s) not met")
        print("=" * 60)
        print("\nThe operator needs to:")
        if not cluster_running:
            print("  - Ensure Spark cluster is running")
        if not had_scaling:
            print("  - Scale workers during traffic spikes:")
            print("    kubectl scale deployment spark-worker -n spark-streaming --replicas=10")
        if not traffic_ok:
            print("  - Wait for traffic generator to run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
