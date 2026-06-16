#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
TARGET_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "8.11.1")
SEED_CONFIGMAP = os.environ.get("BENCH_PARAM_SEED_CONFIGMAP_NAME", "es-seed")
# Hint for the Elasticsearch StatefulSet name. Used as an override when it names
# a StatefulSet that actually exists; otherwise the StatefulSet (and its real
# pod selector label) are detected live from the cluster. The env PERSISTS
# across stages, so a workflow's inherited ES cluster may carry a different
# StatefulSet name/label than this case's standalone default of 'es-cluster'.
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
DEFAULT_SCHEME = "http"
_SCHEME = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_ES_STS = None


def _list_es_statefulsets():
    """Return [(name, app_label_value, creationTimestamp)] for the namespace's
    StatefulSets whose pod template runs an Elasticsearch image."""
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"])
    if res.returncode != 0:
        return []
    try:
        items = json.loads(res.stdout).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for sts in items:
        meta = sts.get("metadata", {})
        spec = sts.get("spec", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        if "elasticsearch" not in " ".join(c.get("image", "") for c in containers):
            continue
        app = (spec.get("selector", {}).get("matchLabels", {}) or {}).get("app")
        out.append((meta.get("name"), app, meta.get("creationTimestamp", "")))
    return out


def resolve_es_sts():
    """Resolve (sts_name, app_label_selector) for the live ES StatefulSet.

    Priority: the BENCH_PARAM_CLUSTER_PREFIX hint when it names a real
    StatefulSet (explicit override wins) -> the single ES StatefulSet detected
    live (oldest first if several). Workflow-agnostic: adapts to an inherited
    cluster whose StatefulSet name/label differ from the standalone default.
    """
    global _ES_STS
    if _ES_STS is not None:
        return _ES_STS
    es_sets = _list_es_statefulsets()
    by_name = {n: (n, a) for (n, a, _c) in es_sets if n}
    if CLUSTER_PREFIX_HINT in by_name:
        name, app = by_name[CLUSTER_PREFIX_HINT]
        _ES_STS = (name, f"app={app}" if app else f"app={name}")
        return _ES_STS
    if es_sets:
        candidates = [s for s in es_sets if s[0]]
        candidates.sort(key=lambda s: (s[2] or ""))
        name, app, _c = candidates[0]
        _ES_STS = (name, f"app={app}" if app else f"app={name}")
        return _ES_STS
    _ES_STS = (CLUSTER_PREFIX_HINT, f"app={CLUSTER_PREFIX_HINT}")
    return _ES_STS


STS_NAME = resolve_es_sts()[0]
APP_LABEL = resolve_es_sts()[1]


def _resolve_expected_nodes(default=3):
    """Node count to enforce (param override -> live Ready es pods -> default).

    The env PERSISTS across stages; adapt the target to the live cluster
    without loosening it (a missing/NotReady node still mismatches).
    """
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_EXPECTED_NODE_COUNT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-l", APP_LABEL, "-o", "json"])
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


EXPECTED_NODES = _resolve_expected_nodes(3)


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic)."""
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--", "/bin/sh", "-c",
        f"curl -s -S -k -o /dev/null -w '%{{http_code}}' --max-time 5 {scheme}://{SERVICE}:9200/",
    ])
    code = (result.stdout or "").strip().strip("'")
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


def curl(path, errors):
    scheme = detect_scheme()
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "/bin/sh",
        "-c",
        f"curl -s -S -k --max-time 10 {scheme}://{SERVICE}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"command terminated with exit code {result.returncode}"
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


def main():
    errors = []

    seed_result = run(["kubectl", "-n", NAMESPACE, "get", "configmap", SEED_CONFIGMAP, "-o", "json"])
    if seed_result.returncode != 0:
        errors.append(f"Failed to read {SEED_CONFIGMAP} ConfigMap: {seed_result.stderr.strip()}")
        index = None
        expected = None
    else:
        try:
            seed = json.loads(seed_result.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse {SEED_CONFIGMAP} ConfigMap JSON")
            seed = {}
        index = seed.get("data", {}).get("index")
        expected = seed.get("data", {}).get("expected_count")

    root = curl("/", errors)
    if isinstance(root, dict):
        version = root.get("version", {}).get("number")
        if version != TARGET_VERSION:
            errors.append(f"Expected version {TARGET_VERSION}, got {version}")
    else:
        errors.append("Failed to read Elasticsearch root version")

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}")
    else:
        errors.append("Failed to read cluster health")

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list) and len(nodes) != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")

    if index and expected:
        count = curl(f"/{index}/_count", errors)
        if isinstance(count, dict):
            actual = count.get("count")
            try:
                expected_val = int(expected)
            except ValueError:
                errors.append(f"Invalid expected_count value: {expected}")
            else:
                if actual != expected_val:
                    errors.append(f"Expected {expected_val} docs in {index}, got {actual}")

    sts_result = run(["kubectl", "-n", NAMESPACE, "get", "sts", STS_NAME, "-o", "json"])
    if sts_result.returncode == 0:
        try:
            sts = json.loads(sts_result.stdout)
        except json.JSONDecodeError:
            sts = {}
        containers = sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if containers:
            image = containers[0].get("image")
            if image and TARGET_VERSION not in image:
                errors.append(f"StatefulSet image not upgraded: {image}")
    else:
        errors.append(f"Failed to read StatefulSet: {sts_result.stderr.strip()}")

    settings = curl("/_cluster/settings", errors)
    if isinstance(settings, dict):
        for scope in ("persistent", "transient"):
            allocation = settings.get(scope, {}).get("cluster", {}).get("routing", {}).get("allocation", {})
            if allocation.get("enable") == "none":
                errors.append("Shard allocation still disabled")
                break

    if errors:
        print("Full restart upgrade verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Full restart upgrade (HA) verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
