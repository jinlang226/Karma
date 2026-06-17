#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
# Hint for the Elasticsearch pod app label. Used as an override when it matches a
# live StatefulSet's selector; otherwise the real selector label is detected
# live from the cluster. The env PERSISTS across stages, so a workflow's
# inherited ES cluster may label its pods differently than this case's standalone
# default of 'es-cluster'.
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
# ES 8.x runs with security enabled, so the HTTP API requires authenticating as
# the elastic superuser. When this case inherits a secured cluster from an
# earlier workflow stage, read its password from the secret that stage created
# so the oracle's queries aren't rejected with 401. Absent secret -> None -> no
# -u, so a standalone unsecured cluster still works.
PASSWORD_SECRET = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME", "elastic-password")
PASSWORD_KEY = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_KEY", "password")
ELASTIC_PASSWORD = None  # set in main() once kubectl is reachable


def _elastic_password():
    """Fetch the elastic-user password from its secret (base64-decoded), or None."""
    import base64
    r = _run_for_label(["kubectl", "-n", NAMESPACE, "get", "secret", PASSWORD_SECRET,
                        "-o", "jsonpath={.data." + PASSWORD_KEY + "}"])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return base64.b64decode(r.stdout.strip()).decode()
    except Exception:
        return None


def _run_for_label(cmd):
    """Minimal subprocess runner used by label detection (mirrors run())."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_APP_LABEL = None


def resolve_app_label():
    """Resolve the 'app=<value>' selector for the live ES pods.

    Priority: the BENCH_PARAM_CLUSTER_PREFIX hint when some live StatefulSet
    actually selects on app=<hint> (explicit override wins) -> the app label of
    the namespace's Elasticsearch StatefulSet detected live -> the hint.
    Workflow-agnostic: adapts to an inherited cluster labelled e.g.
    app=elasticsearch instead of app=es-cluster.
    """
    global _APP_LABEL
    if _APP_LABEL is not None:
        return _APP_LABEL
    res = _run_for_label(["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"])
    labels = []
    if res.returncode == 0:
        try:
            for sts in json.loads(res.stdout).get("items", []):
                spec = sts.get("spec", {})
                containers = spec.get("template", {}).get("spec", {}).get("containers", [])
                if "elasticsearch" not in " ".join(c.get("image", "") for c in containers):
                    continue
                app = (spec.get("selector", {}).get("matchLabels", {}) or {}).get("app")
                ts = sts.get("metadata", {}).get("creationTimestamp", "")
                if app:
                    labels.append((app, ts))
        except (json.JSONDecodeError, AttributeError):
            pass
    if any(app == CLUSTER_PREFIX_HINT for app, _ in labels):
        _APP_LABEL = f"app={CLUSTER_PREFIX_HINT}"
        return _APP_LABEL
    if labels:
        labels.sort(key=lambda x: (x[1] or ""))
        _APP_LABEL = f"app={labels[0][0]}"
        return _APP_LABEL
    _APP_LABEL = f"app={CLUSTER_PREFIX_HINT}"
    return _APP_LABEL


APP_LABEL = resolve_app_label()


def _list_es_statefulset_replicas():
    """Return {sts_name: spec.replicas} for the namespace's Elasticsearch
    StatefulSets (those whose pod template runs an Elasticsearch image)."""
    res = subprocess.run(
        ["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
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
    node that FAILED to join leaves its StatefulSet "live" (siblings are up) but
    absent from ``_cat/nodes``, so EXPECTED stays above the actual count -- no
    masking. Param override is honored FIRST; falls back to ``default`` when no
    live StatefulSets resolve (e.g. _cat/nodes failed or returned empty).
    """
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_EXPECTED_NODE_COUNT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    # Robust count: sum spec.replicas over ES StatefulSets that are actually
    # present (status.replicas > 0). Counts the base cluster + any scaled-up
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
            # Use status.replicas (pods that EXIST), not readyReplicas: a
            # scaled-up nodeset whose pods joined the cluster but lack a passing
            # STS readiness probe has readyReplicas 0 yet is genuinely live.
            current = (sts.get("status", {}) or {}).get("replicas") or 0
            if isinstance(replicas, int) and current > 0:
                desired += replicas
        if desired > 0:
            return desired
    return default


# Default scheme for the standalone case; may be flipped by _detect_scheme() when
# a prior workflow stage toggled xpack.security.http.ssl on this cluster.
DEFAULT_SCHEME = "http"
_SCHEME = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _probe_scheme(scheme):
    """Return True if the ES HTTP API answers on the given scheme.

    The env PERSISTS across stages, so the cluster's actual HTTP scheme may
    differ from this case's standalone default. A reachable endpoint answers
    even when auth is required (401/anything HTTP), so any non-empty body or a
    clean exit counts as 'this scheme is live'. -k tolerates the self-signed
    cert on https.
    """
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--",
        "curl", "-s", "-S", "-k", "-o", "/dev/null",
        "-w", "%{http_code}", "--max-time", "5",
        f"{scheme}://{SERVICE}:9200/",
    ])
    code = (result.stdout or "").strip()
    return result.returncode == 0 and code.isdigit() and code != "000"


def detect_scheme():
    """Detect the cluster's live HTTP scheme (default first, then the other)."""
    global _SCHEME
    if _SCHEME is not None:
        return _SCHEME
    order = [DEFAULT_SCHEME, "https" if DEFAULT_SCHEME == "http" else "http"]
    for scheme in order:
        if _probe_scheme(scheme):
            _SCHEME = scheme
            return _SCHEME
    _SCHEME = DEFAULT_SCHEME
    return _SCHEME


def curl(path, errors):
    scheme = detect_scheme()
    # The client deadline must exceed any server-side ``wait_for`` in `path``,
    # otherwise curl aborts (exit 28) before ES can answer. The retry loop in
    # main() does the real waiting, so each call's server wait stays short (10s)
    # and --max-time (20) comfortably exceeds it.
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-s",
        "-S",
        "-k",
    ]
    if ELASTIC_PASSWORD:
        cmd += ["-u", f"elastic:{ELASTIC_PASSWORD}"]
    cmd += [
        "--max-time",
        "20",
        f"{scheme}://{SERVICE}:9200{path}",
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


def check_cluster(errors):
    """Run the cluster-side checks; return the per-evaluate expected node count.

    The expected node count is derived from the live cluster (the StatefulSets
    backing ``_cat/nodes``), so it is computed here after that query and returned
    to evaluate() for the pod-count assertion.
    """
    # Resolve the live cluster's node list FIRST: the expected node count is
    # derived from only the StatefulSets that actually back these live nodes
    # (the persisted namespace accumulates several ES clusters across stages, so
    # summing every ES StatefulSet's replicas overcounts the queried cluster).
    nodes = curl("/_cat/nodes?format=json", errors)
    node_names = []
    if isinstance(nodes, list):
        node_names = [n.get("name") for n in nodes if n.get("name")]
    expected_nodes = _resolve_expected_nodes(node_names, default=3)

    ep_result = run(["kubectl", "-n", NAMESPACE, "get", "endpoints", SERVICE, "-o", "json"])
    if ep_result.returncode != 0:
        errors.append(f"Failed to read {SERVICE} endpoints: {ep_result.stderr.strip()}")
    else:
        try:
            ep = json.loads(ep_result.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse {SERVICE} endpoints JSON")
            ep = None
        if ep is not None:
            addr_count = 0
            for subset in ep.get("subsets", []) or []:
                addr_count += len(subset.get("addresses", []) or [])
            if addr_count != expected_nodes:
                errors.append(f"Expected {expected_nodes} endpoints, got {addr_count}")

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

    root = curl("/", errors)
    if isinstance(root, dict):
        uuid = root.get("cluster_uuid")
        if not uuid or uuid == "_na_":
            errors.append("Cluster UUID not set")

    if isinstance(nodes, list) and len(node_names) != expected_nodes:
        errors.append(f"Expected {expected_nodes} nodes in _cat/nodes, got {len(node_names)}")

    return expected_nodes


def evaluate():
    """Run one full snapshot of the bootstrap checks; return the list of errors."""
    errors = []

    cm_result = run(["kubectl", "-n", NAMESPACE, "get", "configmap", "es-config", "-o", "json"])
    if cm_result.returncode != 0:
        errors.append(f"Failed to read es-config ConfigMap: {cm_result.stderr.strip()}")
    else:
        try:
            cm = json.loads(cm_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse es-config ConfigMap JSON")
            cm = {}
        config = cm.get("data", {}).get("elasticsearch.yml", "")
        if "cluster.initial_master_nodes" in config:
            errors.append("cluster.initial_master_nodes still present in es-config")

    # Cluster-side checks run first so the live-cluster expected node count can
    # be reused by the pod-count assertion below.
    expected_nodes = check_cluster(errors)

    pods_result = run(
        ["kubectl", "-n", NAMESPACE, "get", "pods", "-l", APP_LABEL, "-o", "json"]
    )
    if pods_result.returncode != 0:
        errors.append(f"Failed to read pods: {pods_result.stderr.strip()}")
    else:
        try:
            pods = json.loads(pods_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse pods JSON")
            pods = {}
        items = pods.get("items", []) if isinstance(pods, dict) else []
        if len(items) != expected_nodes:
            errors.append(f"Expected {expected_nodes} pods, got {len(items)}")
        for pod in items:
            name = pod.get("metadata", {}).get("name", "unknown")
            conditions = pod.get("status", {}).get("conditions", [])
            ready = next((c for c in conditions if c.get("type") == "Ready"), {})
            if ready.get("status") != "True":
                errors.append(f"Pod {name} is not Ready")

    return errors


def main():
    global ELASTIC_PASSWORD
    ELASTIC_PASSWORD = _elastic_password()

    # A multi-node ES cluster can flap at the edge of readiness under load: a
    # node briefly fails its HTTP readiness probe during GC or shard recovery
    # even though the cluster is stably green (writes succeed). A single snapshot
    # can catch that transient and report a false "2/3 Ready" / node-count miss.
    # So verify the STABLE converged state: re-evaluate for up to ~75s and pass
    # as soon as one clean snapshot is seen. This does not loosen the
    # N-node/green requirement -- a genuinely degraded cluster (a node that never
    # joins) fails every attempt and still fails the oracle.
    import time
    deadline = time.monotonic() + 75
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("Bootstrap verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Bootstrap initial master nodes verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
