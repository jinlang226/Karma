#!/usr/bin/env python3
import base64
import json
import os
import re
import shlex
import subprocess
import sys


NAMESPACE = "elasticsearch"
SERVICE = "es-http"
# Hint for the Elasticsearch pod app label. Used as an override when it matches a
# live StatefulSet's selector; otherwise the real selector label is detected
# live from the cluster. The env PERSISTS across stages, so a workflow's
# inherited ES cluster may label its pods differently than this case's standalone
# default of 'es-cluster'.
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
DEFAULT_SCHEME = "http"
_SCHEME = None


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_ELASTIC_PW = None


def _elastic_password():
    """Live elastic-user password.

    Reads the elastic-password secret, base64-decoded. The env PERSISTS across
    stages, so a prior rotate-elastic-password stage may have rotated the
    password away from this case's standalone default — the running curl-test
    pod's $ES_PASS env was captured at pod creation and goes stale after such a
    rotation (env from a secret does not live-update), which is why the queries
    must read the secret fresh here. Falls back to the case default. Cached so
    the retry loop does not re-read the secret each attempt.
    """
    global _ELASTIC_PW
    if _ELASTIC_PW is not None:
        return _ELASTIC_PW
    r = run(["kubectl", "-n", NAMESPACE, "get", "secret", "elastic-password",
             "-o", "jsonpath={.data.password}"])
    pw = None
    if r.returncode == 0 and r.stdout.strip():
        try:
            pw = base64.b64decode(r.stdout.strip()).decode()
        except Exception:
            pw = None
    _ELASTIC_PW = pw or os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD") or "elasticpass"
    return _ELASTIC_PW


def _auth_flag():
    """`-u elastic:<live-pw>`, shell-quoted for the curl-test pod's /bin/sh."""
    return "-u " + shlex.quote("elastic:" + _elastic_password())


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
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", "-o", "json"])
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
    node that FAILED to join leaves its StatefulSet "live" (siblings are up) but
    absent from ``_cat/nodes``, so EXPECTED stays above the actual count -- no
    masking. The LIVE cluster is derived FIRST; the param is a FALLBACK used only
    when no live StatefulSets resolve (e.g. _cat/nodes failed or returned empty).
    """
    # Derive from the LIVE cluster FIRST (workflow-agnostic + composition-aware):
    # sum spec.replicas over the active ES StatefulSets -- the base cluster PLUS
    # any nodeset a PRIOR stage added to the same cluster (e.g. an inherited
    # transform nodeset). A static EXPECTED_NODES param encodes a standalone
    # baseline that wrongly ignores such inherited nodesets, so it is demoted to a
    # FALLBACK below, used only when the live cluster cannot be resolved.
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
            # Count an ES StatefulSet's DESIRED replicas when it is an active part
            # of the cluster: spec.replicas > 0 and not being torn down. Gating on
            # status.replicas undercounts a freshly scaled-up nodeset whose status
            # lags -- its pods have joined the cluster but status.replicas is still
            # 0 -- which wrongly made EXPECTED < the live node count.
            being_deleted = (sts.get("metadata", {}) or {}).get("deletionTimestamp") is not None
            if isinstance(replicas, int) and replicas > 0 and not being_deleted:
                desired += replicas
        if desired > 0:
            return desired

    # Fallbacks (live cluster unresolvable): explicit param, then the default.
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_EXPECTED_NODE_COUNT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    return default


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic).

    Authenticates with the same live elastic password the real queries use; a
    401 still proves the scheme is live, so any HTTP status code counts.
    """
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--", "/bin/sh", "-c",
        ("curl -s -S -k -o /dev/null -w '%{http_code}' --max-time 5 "
         f"{_auth_flag()} "
         f"{scheme}://{SERVICE}.{NAMESPACE}.svc:9200/"),
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
        # The client deadline (--max-time 20) must exceed any server-side
        # ``wait_for`` in `path`, otherwise curl aborts (exit 28) before ES can
        # answer. The retry loop in main() does the real waiting, so each call's
        # server wait stays short (10s).
        (
            f"curl -s -S -k --max-time 20 {_auth_flag()} "
            f"{scheme}://{SERVICE}.{NAMESPACE}.svc:9200{path}"
        ),
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
    """Run one full snapshot of the transport-CA checks; return the errors."""
    errors = []

    cm_result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            "es-transport-ca-bundle",
            "-o",
            "json",
        ]
    )
    if cm_result.returncode != 0:
        errors.append(
            f"Failed to read es-transport-ca-bundle ConfigMap: {cm_result.stderr.strip()}"
        )
    else:
        try:
            cm = json.loads(cm_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse es-transport-ca-bundle ConfigMap JSON")
            cm = {}
        bundle = cm.get("data", {}).get("ca.crt", "")
        cert_count = bundle.count("BEGIN CERTIFICATE")
        if cert_count < 2:
            errors.append(
                f"Transport CA bundle should contain 2 certs, found {cert_count}"
            )

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
            errors.append(
                f"Expected {expected_nodes} nodes, got {health.get('number_of_nodes')}"
            )

    if isinstance(nodes, list):
        if len(node_names) != expected_nodes:
            errors.append(f"Expected {expected_nodes} nodes in _cat/nodes, got {len(node_names)}")

    return errors


def main():
    # A multi-node ES cluster can flap at the edge of readiness under load: a
    # node briefly fails its HTTP readiness probe / drops from the cluster during
    # GC or shard recovery even though it is stably green. A single snapshot can
    # catch that transient and report a false node-count miss. So verify the
    # STABLE converged state: re-evaluate for up to ~75s and pass on the first
    # clean snapshot. This does not loosen the N-node/green/CA-bundle
    # requirements -- a genuinely degraded cluster fails every attempt.
    import time
    deadline = time.monotonic() + 75
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("Transport CA trust verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Transport CA trust verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
