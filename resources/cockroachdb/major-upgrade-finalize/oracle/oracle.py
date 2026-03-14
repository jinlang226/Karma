#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    cluster_pod,
    cluster_prefix,
    cockroach_image,
    parse_tsv,
    run,
    to_bool,
    version_family,
)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    to_version = bench_param("to_version", "24.1.0")

    target_image = cockroach_image(to_version)
    target_version = version_family(to_version)

    errors = []
    expected_replicas = 3

    sts_cmd = ["kubectl", "-n", namespace, "get", "statefulset", prefix, "-o", "json"]
    sts_result = run(sts_cmd)
    if sts_result.returncode == 0:
        try:
            sts_data = json.loads(sts_result.stdout)
            replicas = sts_data.get("spec", {}).get("replicas")
            if isinstance(replicas, int) and replicas > 0:
                expected_replicas = replicas
        except json.JSONDecodeError:
            errors.append("Failed to parse StatefulSet")
    else:
        errors.append(sts_result.stderr.strip() or "Failed to read StatefulSet")

    pods_cmd = [
        "kubectl",
        "-n",
        namespace,
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=cockroachdb",
        "-o",
        "json",
    ]
    pods_result = run(pods_cmd)
    if pods_result.returncode != 0:
        errors.append(pods_result.stderr.strip() or "Failed to read pods")
    else:
        try:
            data = json.loads(pods_result.stdout)
            pods = data.get("items", [])
        except json.JSONDecodeError:
            errors.append("Failed to parse pod list")
            pods = []
        if len(pods) != expected_replicas:
            errors.append(f"Expected {expected_replicas} pods, found {len(pods)}")
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "unknown")
            containers = pod.get("spec", {}).get("containers", [])
            if not containers:
                errors.append(f"No containers found in pod {name}")
                continue
            image = containers[0].get("image")
            if image != target_image:
                errors.append(f"Pod {name} image is {image}; expected {target_image}")

    cluster_version_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "--format=tsv",
        "-e",
        "SHOW CLUSTER SETTING version;",
    ]
    cluster_version_result = run(cluster_version_cmd)
    if cluster_version_result.returncode != 0:
        errors.append(cluster_version_result.stderr.strip() or "Failed to check cluster version")
    else:
        output = cluster_version_result.stdout
        if target_version not in output and str(to_version) not in output:
            errors.append("Cluster version not finalized")

    preserve_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "--format=tsv",
        "-e",
        "SHOW CLUSTER SETTING cluster.preserve_downgrade_option;",
    ]
    preserve_result = run(preserve_cmd)
    if preserve_result.returncode != 0:
        errors.append(
            preserve_result.stderr.strip() or "Failed to check preserve_downgrade_option"
        )
    else:
        lines = []
        for line in preserve_result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "cluster.preserve_downgrade_option":
                continue
            if set(stripped) == {"-"}:
                continue
            lines.append(stripped)
        value = lines[-1] if lines else ""
        if value not in ("", "NULL", "[]"):
            errors.append("preserve_downgrade_option not cleared")

    node_status_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "node",
        "status",
        "--insecure",
        "--format=tsv",
    ]
    node_status_result = run(node_status_cmd)
    if node_status_result.returncode != 0:
        errors.append(node_status_result.stderr.strip() or "Failed to read node status")
    else:
        header, rows = parse_tsv(node_status_result.stdout)
        if not header:
            errors.append("Empty node status output")
        else:
            cols = {name: idx for idx, name in enumerate(header)}
            live_idx = cols.get("is_live")
            if live_idx is None:
                errors.append("Missing is_live column in node status output")
            else:
                live_nodes = 0
                for row in rows:
                    if len(row) <= live_idx:
                        continue
                    if to_bool(row[live_idx]):
                        live_nodes += 1
                if live_nodes != expected_replicas:
                    errors.append(
                        f"Expected {expected_replicas} live nodes, found {live_nodes}"
                    )

    if errors:
        print("Major upgrade finalization verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Major version upgrade finalized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
