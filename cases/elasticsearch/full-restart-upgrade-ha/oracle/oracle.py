#!/usr/bin/env python3
import json
import os
import re
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
# ES 8.x runs with security enabled, so the HTTP API requires authenticating as
# the elastic superuser. When this case inherits a secured cluster from an
# earlier workflow stage, read its password from the secret that stage created
# so the oracle's queries aren't rejected with 401. Absent secret -> None -> no
# -u, so a standalone unsecured cluster still works.
PASSWORD_SECRET = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME", "elastic-password")
PASSWORD_KEY = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_KEY", "password")
DEFAULT_SCHEME = "http"
_SCHEME = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _elastic_password():
    """Fetch the elastic-user password from its secret (base64-decoded), or None."""
    import base64
    r = run(["kubectl", "-n", NAMESPACE, "get", "secret", PASSWORD_SECRET,
             "-o", "jsonpath={.data." + PASSWORD_KEY + "}"])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return base64.b64decode(r.stdout.strip()).decode()
    except Exception:
        return None


ELASTIC_PASSWORD = None  # set in main() once kubectl is reachable


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


def _list_es_statefulset_replicas():
    """Return {sts_name: spec.replicas} for the namespace's Elasticsearch
    StatefulSets (those whose pod template runs an Elasticsearch image)."""
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"])
    if res.returncode != 0:
        return {}
    try:
        items = json.loads(res.stdout).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return {}
    out = {}
    for sts in items:
        spec = sts.get("spec", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        if "elasticsearch" not in " ".join(c.get("image", "") for c in containers):
            continue
        name = sts.get("metadata", {}).get("name")
        replicas = spec.get("replicas")
        if name and isinstance(replicas, int):
            out[name] = replicas
    return out


def _sts_name_for_node(node_name):
    """Map a live ES node name to its backing StatefulSet name.

    An ES node name equals its pod name, which for a StatefulSet pod is
    ``<statefulset-name>-<ordinal>`` (e.g. ``es-cluster-0`` -> ``es-cluster``,
    ``es-data-1`` -> ``es-data``). Strip the trailing ``-<digits>`` ordinal.
    Returns the node name unchanged if it carries no ordinal suffix.
    """
    if not node_name:
        return node_name
    return re.sub(r"-\d+$", "", node_name)


def _live_sts_names(node_names):
    """Set of StatefulSet names actually backing the live cluster nodes.

    Derived by stripping each live node name's ordinal. This excludes
    accumulated/stale/other ES StatefulSets in the (persisted) namespace that
    back no node in the queried cluster.
    """
    return {_sts_name_for_node(n) for n in node_names if n}


def _resolve_expected_nodes(node_names, default=3):
    """Expected node count to enforce, derived from the LIVE cluster.

    The expected total is the DESIRED topology of ONLY the StatefulSets that
    actually back the live cluster (the nodes returned by ``_cat/nodes``): the
    sum of spec.replicas over just those StatefulSets. The namespace PERSISTS
    across workflow stages and accumulates multiple ES clusters, so summing
    replicas across *every* ES StatefulSet overcounts versus the single cluster
    the oracle queries. Restricting to the live cluster's StatefulSets fixes that.

    Using DESIRED spec.replicas (not the live count) keeps the check strict: a
    node that FAILED to rejoin after the restart leaves its StatefulSet "live"
    (siblings are up) but absent from ``_cat/nodes``, so EXPECTED stays above the
    actual count -- no masking. Param override is honored FIRST; falls back to
    ``default`` when no live StatefulSets resolve (e.g. _cat/nodes failed/empty).
    """
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_EXPECTED_NODE_COUNT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    # Robust count: sum spec.replicas over ES StatefulSets that are actually
    # running (readyReplicas > 0). Counts the base cluster + any scaled-up
    # nodeset WITHOUT the fragile node.name -> StatefulSet string mapping (which
    # breaks when a nodeset's node.name differs from its pod name). A torn-down
    # prior cluster's StatefulSet has readyReplicas 0 and is excluded. Still
    # strict: a node that failed to join leaves its STS ready>0 but short, so the
    # spec-replica sum exceeds the live node count and the check still fails.
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"])
    if res.returncode == 0:
        try:
            items = json.loads(res.stdout).get("items", [])
        except Exception:
            items = []
        desired = 0
        for sts in items:
            spec = sts.get("spec", {}) or {}
            containers = spec.get("template", {}).get("spec", {}).get("containers", []) or []
            if "elasticsearch" not in " ".join(c.get("image", "") for c in containers):
                continue
            replicas = spec.get("replicas")
            ready = (sts.get("status", {}) or {}).get("readyReplicas") or 0
            if isinstance(replicas, int) and ready > 0:
                desired += replicas
        if desired > 0:
            return desired
    return default


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
    auth = ""
    if ELASTIC_PASSWORD:
        import shlex
        auth = f"-u {shlex.quote('elastic:' + ELASTIC_PASSWORD)} "
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "/bin/sh",
        "-c",
        # The client deadline (--max-time 20) must exceed any server-side
        # ``wait_for`` in `path`, otherwise curl aborts (exit 28) before ES can
        # answer. The retry loop in main() does the real waiting, so each call's
        # server wait stays short (10s).
        f"curl -s -S -k {auth}--max-time 20 {scheme}://{SERVICE}:9200{path}",
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


def evaluate():
    """Run one full snapshot of the upgrade checks; return the list of errors."""
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

    # Resolve the live cluster's node list FIRST: the expected node count is
    # derived from only the StatefulSets that actually back these live nodes
    # (the persisted namespace accumulates several ES clusters across stages, so
    # summing every ES StatefulSet's replicas overcounts the queried cluster).
    nodes = curl("/_cat/nodes?format=json", errors)
    node_names = []
    if isinstance(nodes, list):
        node_names = [n.get("name") for n in nodes if n.get("name")]
    expected_nodes = _resolve_expected_nodes(node_names, default=3)

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={expected_nodes}&timeout=10s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != expected_nodes:
            errors.append(f"Expected {expected_nodes} nodes, got {health.get('number_of_nodes')}")
    else:
        errors.append("Failed to read cluster health")

    if isinstance(nodes, list) and len(node_names) != expected_nodes:
        errors.append(f"Expected {expected_nodes} nodes in _cat/nodes, got {len(node_names)}")

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

    return errors


def main():
    global ELASTIC_PASSWORD
    ELASTIC_PASSWORD = _elastic_password()

    # A multi-node ES cluster can flap at the edge of readiness under load: a
    # node briefly fails its HTTP readiness probe / drops from the cluster during
    # GC or shard recovery even though it is stably green. A single snapshot can
    # catch that transient and report a false node-count miss. So verify the
    # STABLE converged state: re-evaluate for up to ~75s and pass on the first
    # clean snapshot. This does not loosen the version/N-node/green/doc-count
    # requirements -- a genuinely degraded cluster fails every attempt.
    import time
    deadline = time.monotonic() + 75
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("Full restart upgrade verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Full restart upgrade (HA) verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
