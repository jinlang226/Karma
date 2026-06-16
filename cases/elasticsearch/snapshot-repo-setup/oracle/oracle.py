#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = "elasticsearch"
SERVICE = "es-http"
# Hint for the Elasticsearch StatefulSet name. Used as an override when it names
# a StatefulSet that actually exists; otherwise the StatefulSet (and its real
# pod selector label) are detected live from the cluster. The env PERSISTS
# across stages, so a workflow's inherited ES cluster may carry a different
# StatefulSet name/label than this case's standalone default of 'es-cluster'.
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
REPO_NAME = "minio-repo"
KEYS = {
    "s3.client.default.access_key",
    "s3.client.default.secret_key",
}
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
    # Authoritative inherited topology = the StatefulSet's desired replicas. A
    # prior stage (e.g. safe-downscale) may have scaled the cluster down; read
    # spec.replicas rather than a Ready-pod count because a downscaled single
    # node is YELLOW and so fails its readiness probe (Ready=False) while still
    # being a fully functional node -- a Ready count would undercount to 0 and
    # wrongly fall back to the default.
    sts = run(["kubectl", "-n", NAMESPACE, "get", "statefulset", STS_NAME,
               "-o", "jsonpath={.spec.replicas}"])
    if sts.returncode == 0 and sts.stdout.strip().isdigit():
        desired = int(sts.stdout.strip())
        if desired > 0:
            return desired
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
        f"curl -s -S -k -o /dev/null -w '%{{http_code}}' --max-time 5 {scheme}://{SERVICE}.{NAMESPACE}.svc:9200/",
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
        f"curl -s -S -k --max-time 10 {scheme}://{SERVICE}.{NAMESPACE}.svc:9200{path}",
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


def get_pods(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            APP_LABEL,
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        errors.append(f"Failed to list Elasticsearch pods: {result.stderr.strip()}")
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse pod list JSON")
        return []
    return [item.get("metadata", {}).get("name") for item in payload.get("items", [])]


def check_keystore(pod, errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            pod,
            "--",
            "/usr/share/elasticsearch/bin/elasticsearch-keystore",
            "list",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        errors.append(f"Failed to list keystore on {pod}: {detail}")
        return
    keys = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    missing = sorted(KEYS - keys)
    if missing:
        errors.append(f"Missing keystore keys on {pod}: {', '.join(missing)}")


def check_configmap(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            "es-config",
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        errors.append(f"Failed to read es-config ConfigMap: {result.stderr.strip()}")
        return
    try:
        cm = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse es-config ConfigMap JSON")
        return
    text = json.dumps(cm)
    if "access_key" in text or "secret_key" in text:
        errors.append("Plaintext credentials found in es-config ConfigMap")


def check_snapshots(errors):
    repo = curl(f"/_snapshot/{REPO_NAME}", errors)
    if repo is None:
        return
    if "error" in repo:
        errors.append(f"Snapshot repository {REPO_NAME} not found")
        return

    snaps = curl(f"/_snapshot/{REPO_NAME}/_all", errors)
    if not isinstance(snaps, dict):
        return
    snapshots = snaps.get("snapshots", [])
    if not snapshots:
        errors.append("No snapshots found in repository")
        return
    success = [s for s in snapshots if s.get("state") == "SUCCESS"]
    if not success:
        errors.append("No SUCCESS snapshots found in repository")


def main():
    errors = []

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=30s",
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

    check_snapshots(errors)
    check_configmap(errors)

    pods = get_pods(errors)
    if pods:
        for pod in pods:
            check_keystore(pod, errors)
    else:
        errors.append("No Elasticsearch pods found to verify keystore")

    if errors:
        print("Secure settings verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Secure settings verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
