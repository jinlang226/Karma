#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
ES_IMAGE = os.environ.get(
    "BENCH_PARAM_TARGET_IMAGE", "docker.elastic.co/elasticsearch/elasticsearch:8.11.1"
)
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
# ES 8.x runs with security enabled, so the HTTPS API requires authenticating as
# the elastic superuser. Read its password from the secret the precondition
# created so the oracle's queries aren't rejected with 401.
PASSWORD_SECRET = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME", "elastic-password")
PASSWORD_KEY = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD_KEY", "password")
# ES 8.x deploys with HTTP TLS on, so https is the standalone default; a prior
# stage could have disabled it, so _detect_scheme() flips to http when needed.
DEFAULT_SCHEME = "https"
_SCHEME = None


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


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _resolve_expected_nodes(default=3):
    """Node count to enforce (param override -> live ES pods by image -> default).

    The env PERSISTS across stages; adapt the target to whatever the cluster
    accumulated without loosening it (a missing node still mismatches).
    """
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_EXPECTED_NODE_COUNT"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-o", "json"])
    if res.returncode == 0:
        try:
            items = json.loads(res.stdout).get("items", [])
            ready = 0
            for pod in items:
                imgs = [c.get("image") for c in pod.get("spec", {}).get("containers", [])]
                if ES_IMAGE not in imgs:
                    continue
                conds = pod.get("status", {}).get("conditions", [])
                if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conds):
                    ready += 1
            if ready > 0:
                return ready
        except (json.JSONDecodeError, AttributeError):
            pass
    return default


EXPECTED_NODES = _resolve_expected_nodes(3)


def _probe_scheme(scheme):
    """True if the ES HTTP API answers on the given scheme (auth-agnostic).

    A 401 still means the scheme is live, so any HTTP status code counts.
    """
    cmd = ["kubectl", "-n", NAMESPACE, "exec", "curl-test", "--",
           "curl", "-s", "-S", "-k", "-o", "/dev/null", "-w", "%{http_code}",
           "--max-time", "5"]
    if ELASTIC_PASSWORD:
        cmd += ["-u", f"elastic:{ELASTIC_PASSWORD}"]
    cmd += [f"{scheme}://{SERVICE}:9200/"]
    result = run(cmd)
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


def get_es_pods(errors):
    result = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-o", "json"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to list pods: {detail}")
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse pod list JSON")
        return []

    pods = []
    for pod in data.get("items", []):
        for container in pod.get("spec", {}).get("containers", []):
            if container.get("image") == ES_IMAGE:
                pods.append(pod)
                break
    return pods


def pod_ready(pod):
    for condition in pod.get("status", {}).get("conditions", []):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def curl_json(path, errors):
    cmd = [
        "kubectl", "-n", NAMESPACE, "exec", "curl-test", "--",
        "curl", "-s", "-S", "-k",
    ]
    if ELASTIC_PASSWORD:
        cmd += ["-u", f"elastic:{ELASTIC_PASSWORD}"]
    cmd += ["--max-time", "10", f"{detect_scheme()}://{SERVICE}:9200{path}"]
    result = run(cmd)
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


def main():
    global ELASTIC_PASSWORD
    errors = []

    ELASTIC_PASSWORD = _elastic_password()

    es_pods = get_es_pods(errors)
    if len(es_pods) != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} Elasticsearch pods, found {len(es_pods)}")
    ready_count = sum(1 for pod in es_pods if pod_ready(pod))
    if ready_count != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} Ready Elasticsearch pods, found {ready_count}")

    health = curl_json(
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

    root = curl_json("/", errors)
    if isinstance(root, dict):
        cluster_name = root.get("cluster_name")
        if not cluster_name:
            errors.append("Elasticsearch root response missing cluster_name")
    stats = curl_json("/_cluster/stats", errors)
    if isinstance(stats, dict):
        nodes = stats.get("nodes", {}).get("count", {}).get("total")
        if nodes != EXPECTED_NODES:
            errors.append(f"Cluster stats expected {EXPECTED_NODES} nodes, got {nodes}")

    if errors:
        print("Deploy core cluster verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Deploy core cluster verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
