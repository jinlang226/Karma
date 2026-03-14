#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CURL_POD = os.environ.get("BENCH_PARAM_CURL_POD_NAME", "curl-test")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_TARGET_MASTER_NODES", "1"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path, errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CURL_POD,
            "--",
            "curl",
            "-s",
            "-S",
            "--max-time",
            "5",
            f"http://{SERVICE}:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to query {path}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {path}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from {path}")
        return None


def get_nodes(errors):
    nodes = curl_json("/_cat/nodes?format=json&h=name,node.role", errors)
    if not isinstance(nodes, list):
        return None
    return nodes


def get_exclusions(errors):
    state = curl_json(
        "/_cluster/state?filter_path=metadata.cluster_coordination.voting_config_exclusions",
        errors,
    )
    if not isinstance(state, dict):
        return None
    return state.get("metadata", {}).get("cluster_coordination", {}).get(
        "voting_config_exclusions", []
    )


def is_auto_shrink_enabled(errors):
    settings = curl_json(
        "/_cluster/settings?filter_path=persistent.cluster.auto_shrink_voting_configuration,"
        "transient.cluster.auto_shrink_voting_configuration",
        errors,
    )
    if not isinstance(settings, dict):
        return None

    def value_is_false(value):
        if value is None:
            return False
        if isinstance(value, bool):
            return not value
        if isinstance(value, str):
            return value.strip().lower() == "false"
        return False

    persistent = (
        settings.get("persistent", {})
        .get("cluster", {})
        .get("auto_shrink_voting_configuration")
    )
    transient = (
        settings.get("transient", {})
        .get("cluster", {})
        .get("auto_shrink_voting_configuration")
    )
    if value_is_false(persistent) or value_is_false(transient):
        return False
    return True


def main():
    errors = []

    health = curl_json("/_cluster/health?timeout=5s", errors)
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health expected yellow/green, got {status}")

    nodes = get_nodes(errors)
    if isinstance(nodes, list):
        if len(nodes) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes, got {len(nodes)}")
        masters = [
            n
            for n in nodes
            if "m" in (n.get("roles") or n.get("node.role") or "")
        ]
        if len(masters) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} master-eligible nodes, got {len(masters)}")

    exclusions = get_exclusions(errors)
    if exclusions:
        errors.append("Voting exclusions were not cleared")

    auto_shrink = is_auto_shrink_enabled(errors)
    if auto_shrink is False:
        errors.append("auto_shrink_voting_configuration is disabled")

    if errors:
        print("Master downscale recovery verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Master downscale recovery verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
