#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CURL_POD = os.environ.get("BENCH_PARAM_CURL_POD_NAME", "curl-test")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
DEFAULT_SCHEME = "http"
_SCHEME = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _resolve_expected_nodes(default=1):
    """Target master/node count (param override -> live Ready es pods -> default).

    The env PERSISTS across stages, so adapt the topology target to the live
    cluster without loosening it. The explicit downscale-target param wins; the
    live count is the fallback.
    """
    for key in ("BENCH_PARAM_TARGET_MASTER_NODES", "BENCH_PARAM_EXPECTED_NODES"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"])
    if res.returncode == 0:
        try:
            items = json.loads(res.stdout).get("items", [])
            ready = sum(
                1 for p in items
                if any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in p.get("status", {}).get("conditions", []))
            )
            if ready > 0:
                return ready
        except (json.JSONDecodeError, AttributeError):
            pass
    return default


EXPECTED_NODES = _resolve_expected_nodes(1)


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic)."""
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", CURL_POD, "--",
        "curl", "-s", "-S", "-k", "-o", "/dev/null",
        "-w", "%{http_code}", "--max-time", "5", f"{scheme}://{SERVICE}:9200/",
    ])
    code = (result.stdout or "").strip()
    return result.returncode == 0 and code.isdigit() and code != "000"


def detect_scheme():
    """Detect the cluster's live HTTP scheme (default first, then the other)."""
    global _SCHEME
    if _SCHEME is not None:
        return _SCHEME
    for scheme in (DEFAULT_SCHEME, "https" if DEFAULT_SCHEME == "http" else "http"):
        if _probe_scheme(scheme):
            _SCHEME = scheme
            return _SCHEME
    _SCHEME = DEFAULT_SCHEME
    return _SCHEME


def curl_json(path, errors):
    scheme = detect_scheme()
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
            "-k",
            "--max-time",
            "5",
            f"{scheme}://{SERVICE}:9200{path}",
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
