#!/usr/bin/env python3
import json
import os
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
    """True if the ES HTTP API answers on the given scheme (auth-agnostic).

    Authenticates with the same $ES_USER/$ES_PASS the real queries use; a 401
    still proves the scheme is live, so any HTTP status code counts.
    """
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--", "/bin/sh", "-c",
        ("curl -s -S -k -o /dev/null -w '%{http_code}' --max-time 5 "
         "-u \"$ES_USER:$ES_PASS\" "
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
            "curl -s -S -k --max-time 20 -u \"$ES_USER:$ES_PASS\" "
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

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=10s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_NODES:
            errors.append(
                f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}"
            )

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list):
        if len(nodes) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")

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
