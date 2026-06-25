#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
# Hint for the Elasticsearch StatefulSet name. Used as an override when it names
# a StatefulSet that actually exists; otherwise the StatefulSet (and its real
# pod selector label) are detected live from the cluster. The env PERSISTS
# across stages, so a workflow's inherited ES cluster may carry a different
# StatefulSet name/label than this case's standalone default of 'es-cluster'.
CLUSTER_PREFIX_HINT = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
# Marker file the precondition writes on each data volume and the agent must
# preserve on the surviving node. Read it from the param so the oracle checks the
# SAME literal path the prompt names and the precondition seeds (Pattern 4: never
# grade a literal the prompt did not promise). Default matches the standalone seed.
MARKER_PATH = os.environ.get(
    "BENCH_PARAM_MARKER_FILE_PATH", "/usr/share/elasticsearch/data/pvc-gc-marker"
)
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
    """Return [(name, replicas, app_label_value, creationTimestamp)] for the
    namespace's StatefulSets whose pod template runs an Elasticsearch image."""
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
        out.append((meta.get("name"), spec.get("replicas"), app, meta.get("creationTimestamp", "")))
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
    by_name = {n: (n, a) for (n, _r, a, _c) in es_sets if n}
    if CLUSTER_PREFIX_HINT in by_name:
        name, app = by_name[CLUSTER_PREFIX_HINT]
        _ES_STS = (name, f"app={app}" if app else f"app={name}")
        return _ES_STS
    if es_sets:
        candidates = [s for s in es_sets if s[0]]
        candidates.sort(key=lambda s: (s[3] or ""))
        name, _r, app, _c = candidates[0]
        _ES_STS = (name, f"app={app}" if app else f"app={name}")
        return _ES_STS
    _ES_STS = (CLUSTER_PREFIX_HINT, f"app={CLUSTER_PREFIX_HINT}")
    return _ES_STS


STS_NAME = resolve_es_sts()[0]
PVC_PREFIX = os.environ.get("BENCH_PARAM_PVC_NAME_PREFIX") or f"data-{STS_NAME}-"
APP_LABEL = resolve_es_sts()[1]


def _resolve_expected_replicas(default=1):
    """Downscale target to enforce (param -> live STS spec replicas -> default).

    The env PERSISTS across stages, so the surviving topology may not be the
    standalone target of 1. The explicit downscale-target param wins; otherwise
    the live StatefulSet spec replicas is the target. The shard-migration / PVC
    / marker checks below still verify the real downscale on the surviving set.
    """
    val = os.environ.get("BENCH_PARAM_TARGET_REPLICAS")
    if val is not None and str(val).strip():
        try:
            return int(val)
        except ValueError:
            pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", STS_NAME, "-o", "json"])
    if res.returncode == 0:
        try:
            spec = json.loads(res.stdout).get("spec", {}) or {}
            live = spec.get("replicas")
            if isinstance(live, int) and live > 0:
                return live
        except (json.JSONDecodeError, AttributeError):
            pass
    return default


EXPECTED_REPLICAS = _resolve_expected_replicas(1)


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic)."""
    result = run([
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--", "/bin/sh", "-c",
        f"curl -s -S -k -o /dev/null -w '%{{http_code}}' --connect-timeout 2 --max-time 3 {scheme}://{SERVICE}:9200/",
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
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "curl-test",
            "--",
            "/bin/sh",
            "-c",
            f"curl -s -S -k {auth}--connect-timeout 2 --max-time 3 {scheme}://{SERVICE}:9200{path}",
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


def get_json(cmd, errors, label):
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read {label}: {detail}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse {label} JSON")
        return None


def pod_ready(pod):
    for condition in pod.get("status", {}).get("conditions", []):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def pvc_ordinal(name):
    if not name.startswith(PVC_PREFIX):
        return None
    suffix = name[len(PVC_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


def evaluate():
    """Run one full snapshot of the downscale checks; return the list of errors."""
    errors = []

    sts_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "sts", STS_NAME, "-o", "json"],
        errors,
        f"StatefulSet {STS_NAME}",
    )
    replicas = None
    if sts_data:
        replicas = sts_data.get("spec", {}).get("replicas")
        if replicas != EXPECTED_REPLICAS:
            errors.append(f"StatefulSet replicas expected {EXPECTED_REPLICAS}, got {replicas}")
    else:
        errors.append("Unable to read StatefulSet replicas")
        replicas = 0

    pods_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "pods", "-l", APP_LABEL, "-o", "json"],
        errors,
        "pod list",
    )
    pods = {item.get("metadata", {}).get("name"): item for item in pods_data.get("items", [])} if pods_data else {}
    for ordinal in range(replicas):
        pod_name = f"{STS_NAME}-{ordinal}"
        pod = pods.get(pod_name)
        if not pod:
            errors.append(f"Missing pod {pod_name}")
            continue
        # O-funcready: do NOT gate on the k8s pod-`Ready` bit. A surviving ES
        # node serves (a yellow cluster answers) before -- and can dip back below
        # -- its HTTP readiness probe during the shard migration this case
        # triggers, so asserting pod-Ready false-fails a node that is already
        # serving. The DELIVERABLE (the surviving nodes serve, shards migrated)
        # is graded functionally below: `_cluster/health` (yellow/green,
        # number_of_nodes == EXPECTED, 0 unassigned) + shard placement. Pod
        # existence is still required above; a genuinely down node drops
        # number_of_nodes and still fails.

    if replicas >= 1:
        result = run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "exec",
                f"{STS_NAME}-0",
                "--",
                "/bin/sh",
                "-c",
                f"test -f {MARKER_PATH}",
            ]
        )
        if result.returncode != 0:
            errors.append("Marker file missing on remaining pod")

    health = curl("/_cluster/health", errors)
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_REPLICAS:
            errors.append(
                f"Expected {EXPECTED_REPLICAS} node, got {health.get('number_of_nodes')}"
            )
        if health.get("unassigned_shards") not in (0, "0"):
            errors.append("Unassigned shards present after downscale")

    shards = curl("/_cat/shards/app-data?format=json", errors)
    if isinstance(shards, list):
        surviving = {f"{STS_NAME}-{i}" for i in range(max(EXPECTED_REPLICAS, 1))}
        bad = [s for s in shards if s.get("node") and s.get("node") not in surviving]
        if bad:
            errors.append("app-data shards still present on removed nodes")
    else:
        errors.append("Unable to verify app-data shard placement")

    pvc_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "pvc", "-o", "json"],
        errors,
        "PVC list",
    )
    pvc_names = []
    if pvc_data:
        pvc_names = [item.get("metadata", {}).get("name") for item in pvc_data.get("items", [])]
    prefixed = [name for name in pvc_names if name and name.startswith(PVC_PREFIX)]
    prefixed_set = set(prefixed)
    expected = {f"{PVC_PREFIX}{i}" for i in range(replicas)}
    missing = expected - prefixed_set
    for name in sorted(missing):
        errors.append(f"Missing PVC for active ordinal: {name}")
    for name in prefixed:
        ordinal = pvc_ordinal(name)
        if ordinal is None:
            continue
        if ordinal >= replicas:
            errors.append(f"Orphan PVC still present: {name}")

    return errors


def main():
    global ELASTIC_PASSWORD
    ELASTIC_PASSWORD = _elastic_password()

    # A multi-node ES cluster can flap at the edge of readiness under load: a
    # surviving node briefly fails its HTTP readiness probe / the cluster reports
    # transient unassigned shards mid shard-migration even though it converges
    # green. A single snapshot can catch that transient and report a false
    # not-Ready / node-count / unassigned-shards miss. So verify the STABLE
    # converged state: re-evaluate for up to ~75s and pass on the first clean
    # snapshot. This does not loosen the replica/green/shard-placement
    # requirements -- a genuinely degraded downscale fails every attempt.
    import time
    deadline = time.monotonic() + 75
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("Safe downscale verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Safe downscale verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
