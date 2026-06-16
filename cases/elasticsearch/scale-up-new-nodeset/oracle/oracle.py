#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
# Hint for the original StatefulSet name. Used as an override when it names an
# StatefulSet that actually exists; otherwise the original is detected live from
# the cluster (the env PERSISTS across stages, so a workflow's inherited ES
# cluster may carry a different StatefulSet name than this case's standalone
# default).
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
INDEX_NAME = os.environ.get("BENCH_PARAM_INDEX_NAME", "app-data")
ORIGINAL_REPLICAS = int(os.environ.get("BENCH_PARAM_ORIGINAL_REPLICAS", "3"))
DEFAULT_SCHEME = "http"
_SCHEME = None
_CREDS = None
_ORIGINAL_STS = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _list_es_statefulsets():
    """Return the StatefulSet items in NAMESPACE whose pod template runs an
    Elasticsearch image. Returns a list of (name, replicas, creationTimestamp)."""
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
        imgs = " ".join(c.get("image", "") for c in containers)
        if "elasticsearch" not in imgs:
            continue
        out.append((
            meta.get("name"),
            spec.get("replicas"),
            meta.get("creationTimestamp", ""),
        ))
    return out


def resolve_original_sts():
    """Resolve the ORIGINAL Elasticsearch StatefulSet name (pre-scale-up).

    Priority:
    1. The BENCH_PARAM_CLUSTER_PREFIX hint, IF it names a StatefulSet that
       actually exists in the cluster (explicit override wins).
    2. Live detection: among the namespace's Elasticsearch StatefulSets, the
       original is the one whose replicas match ORIGINAL_REPLICAS (the new
       nodeset is a separate StatefulSet); ties broken by oldest
       creationTimestamp. This makes the oracle workflow-agnostic regardless of
       the inherited cluster's StatefulSet name (e.g. 'es' vs 'es-cluster').
    3. The hint as a last resort (so error messages name something concrete).
    """
    global _ORIGINAL_STS
    if _ORIGINAL_STS is not None:
        return _ORIGINAL_STS

    es_sets = _list_es_statefulsets()
    names = {n for (n, _r, _c) in es_sets if n}

    # 1. Honor an explicit hint that points at a real StatefulSet.
    if CLUSTER_PREFIX_HINT in names:
        _ORIGINAL_STS = CLUSTER_PREFIX_HINT
        return _ORIGINAL_STS

    if es_sets:
        # 2a. Prefer the StatefulSet still at the original replica count.
        at_original = [s for s in es_sets if s[1] == ORIGINAL_REPLICAS and s[0]]
        candidates = at_original or [s for s in es_sets if s[0]]
        # Oldest first — the original cluster predates any new nodeset.
        candidates.sort(key=lambda s: (s[2] or ""))
        if candidates:
            _ORIGINAL_STS = candidates[0][0]
            return _ORIGINAL_STS

    # 3. Nothing detected; fall back to the hint (will surface a NotFound).
    _ORIGINAL_STS = CLUSTER_PREFIX_HINT
    return _ORIGINAL_STS


def _detect_creds():
    """Return '-u elastic:<password>' flag string if auth is needed, else ''."""
    global _CREDS
    if _CREDS is not None:
        return _CREDS
    # check explicit env override first
    pw = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD", "")
    if not pw:
        # try reading from the live secret used by the cluster
        for secret_name in ("elastic-password", "elastic-credentials"):
            res = run([
                "kubectl", "-n", NAMESPACE, "get", "secret", secret_name,
                "-o", "jsonpath={.data.password}",
            ])
            if res.returncode == 0 and res.stdout.strip():
                import base64
                try:
                    pw = base64.b64decode(res.stdout.strip()).decode()
                    break
                except Exception:
                    pass
    _CREDS = f"-u elastic:{pw}" if pw else ""
    return _CREDS


def _resolve_expected_nodes(default=5):
    """Total node count to enforce after scale-up.

    The expected total is the DESIRED topology: the sum of spec.replicas across
    every Elasticsearch StatefulSet in the namespace (original cluster + any new
    nodeset, plus any extra nodeset an earlier workflow stage left behind, e.g.
    a dedicated transform node). Deriving from desired replicas — not a
    point-in-time Ready-pod count — makes this adapt to whatever topology the
    persisted env accumulated AND avoids racing a node that is still joining.

    BENCH_PARAM_EXPECTED_NODES is honored ONLY as an explicit override when it is
    at least as large as the live desired total: the case ships a standalone
    default of 5, which would otherwise wrongly pin the count below an inherited
    cluster that already carries more nodes. The 'at least 2 new nodes' and
    original-StatefulSet checks below still enforce the real task.
    """
    desired = None
    es_sets = _list_es_statefulsets()
    replica_counts = [r for (_n, r, _c) in es_sets if isinstance(r, int)]
    if replica_counts:
        desired = sum(replica_counts)

    val = os.environ.get("BENCH_PARAM_EXPECTED_NODES")
    if val is not None and str(val).strip():
        try:
            override = int(val)
            # Only let the param raise the bar, never lower it below the live
            # desired topology (defeats the stale standalone default in a
            # workflow with an inherited, larger cluster).
            if desired is None or override >= desired:
                return override
        except ValueError:
            pass

    if desired is not None and desired > 0:
        return desired
    return default


EXPECTED_NODES = _resolve_expected_nodes(5)


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic)."""
    creds = _detect_creds()
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--", "/bin/sh", "-c",
        f"curl -s -S -k {creds} -o /dev/null -w '%{{http_code}}' --max-time 5 {scheme}://{SERVICE}:9200/",
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
    creds = _detect_creds()
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
        f"curl -s -S -k {creds} --max-time 20 {scheme}://{SERVICE}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"command terminated with exit code {result.returncode}"
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


def get_original_nodes(errors):
    # Query all pods and filter by StatefulSet ownerReference; the app label
    # may not match the original StatefulSet if a prior stage reconfigured it.
    original_sts = resolve_original_sts()
    result = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-o", "json"])
    if result.returncode != 0:
        errors.append(f"Failed to list Elasticsearch pods: {result.stderr.strip()}")
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse pod list JSON")
        return []

    names = []
    for item in payload.get("items", []):
        for owner in item.get("metadata", {}).get("ownerReferences", []):
            if owner.get("kind") == "StatefulSet" and owner.get("name") == original_sts:
                names.append(item.get("metadata", {}).get("name"))
                break
    return names


def get_sts_replicas(errors):
    original_sts = resolve_original_sts()
    result = run(["kubectl", "-n", NAMESPACE, "get", "sts", original_sts, "-o", "json"])
    if result.returncode != 0:
        errors.append(f"Failed to read StatefulSet {original_sts}: {result.stderr.strip()}")
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse StatefulSet JSON")
        return None
    return payload.get("spec", {}).get("replicas")


# ES system/built-in node attributes (not operator-set allocation attributes).
# These vary across nodes for reasons unrelated to allocation awareness (a
# transform/ml node carries transform.node=true, ml.* memory sizing differs by
# pod, etc.), so they must be excluded when deciding whether the agent applied a
# *distinguishing allocation attribute*.
_BUILTIN_ATTR_PREFIXES = ("ml.", "xpack.", "transform.")


def _is_builtin_attr(key):
    return any(key.startswith(p) for p in _BUILTIN_ATTR_PREFIXES)


def attribute_differs(attributes_by_node, original_nodes, new_nodes):
    # The new nodeset is "distinguished" when some new node carries a custom
    # allocation attribute (key,value) that no original node has -- covering BOTH
    # a brand-new key the originals lack (node.attr.rack=new on a scaled-up data
    # nodeset while the originals have no rack) AND a shared key with a different
    # value (node.attr.zone=new on the new nodes while the originals carry
    # node.attr.zone=<other>). ES built-in attributes (transform.node, xpack.*,
    # ml.*) are excluded so a transform/ml node is not mistaken for a
    # distinguishing allocation attribute, and the new nodes need NOT be
    # homogeneous. An agent that added no custom allocation attribute leaves the
    # new nodes carrying only keys/values the originals also have, so this fails.
    if not original_nodes or not new_nodes:
        return False
    original_pairs = set()
    for n in original_nodes:
        for key, value in attributes_by_node.get(n, {}).items():
            if not _is_builtin_attr(key):
                original_pairs.add((key, value))
    for n in new_nodes:
        for key, value in attributes_by_node.get(n, {}).items():
            if _is_builtin_attr(key) or value in (None, ""):
                continue
            if (key, value) not in original_pairs:
                return True
    return False


def evaluate():
    """Run one full snapshot of the scale-up checks; return the list of errors."""
    errors = []

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=10s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_NODES:
            # Diagnostic: when the desired-replica total disagrees with the live
            # node count, dump the per-StatefulSet replica breakdown so an
            # over-provisioned / inherited nodeset is visible in the verdict.
            print("[diag] es statefulsets (name,replicas,created)=%s" % (_list_es_statefulsets(),), file=sys.stderr)
            errors.append(f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}")

    nodes = curl("/_cat/nodes?format=json", errors)
    node_names = []
    if isinstance(nodes, list):
        node_names = [n.get("name") for n in nodes if n.get("name")]
        if len(node_names) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(node_names)}")

    original_nodes = get_original_nodes(errors)
    if original_nodes:
        new_nodes = [n for n in node_names if n not in original_nodes]
        if len(new_nodes) < 2:
            errors.append("Expected at least 2 new nodes outside original StatefulSet")
    else:
        new_nodes = []
        errors.append("Unable to determine original StatefulSet nodes")

    attrs = curl("/_nodes?filter_path=nodes.*.name,nodes.*.attributes", errors)
    if isinstance(attrs, dict):
        attributes_by_node = {}
        for node in attrs.get("nodes", {}).values():
            name = node.get("name")
            if name:
                attributes_by_node[name] = node.get("attributes", {})
        if original_nodes and new_nodes and not attribute_differs(attributes_by_node, original_nodes, new_nodes):
            errors.append("No allocation attribute differs between original nodes and new nodes")

    shards = curl(f"/_cat/shards/{INDEX_NAME}?format=json", errors)
    if isinstance(shards, list) and new_nodes:
        on_new = [s for s in shards if s.get("node") in new_nodes]
        if not on_new:
            errors.append(f"No {INDEX_NAME} shards found on new nodes")

    replicas = get_sts_replicas(errors)
    if replicas is not None and replicas != ORIGINAL_REPLICAS:
        errors.append(f"StatefulSet {resolve_original_sts()} replicas expected {ORIGINAL_REPLICAS}, got {replicas}")

    return errors


def main():
    # A multi-node ES cluster can flap at the edge of readiness under load: a
    # node briefly fails its HTTP readiness probe / drops from the cluster during
    # GC or shard recovery even though it is stably green. A single snapshot can
    # catch that transient and report a false node-count miss. So verify the
    # STABLE converged state: re-evaluate for up to ~75s and pass on the first
    # clean snapshot. This does not loosen the N-node/green/shard-placement
    # requirements -- a genuinely degraded cluster fails every attempt.
    import time
    deadline = time.monotonic() + 150
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("Scale-up nodeset verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Scale-up new nodeset verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
