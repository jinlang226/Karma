#!/usr/bin/env python3
import json
import subprocess
import sys

MON_NS = "monitoring"
ES_NS = "elasticsearch"
MON_SERVICE = "monitoring-es-http"
MON_CURL = "monitoring-curl-test"
ES_APP_LABEL = "es-cluster"
DEFAULT_SCHEME = "http"
_SCHEME = {}


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _probe_scheme(service, scheme):
    """True if the monitoring ES HTTP API answers on the given scheme.

    The env PERSISTS across stages, so the monitoring cluster's live scheme may
    differ from the default; a 401 still proves the scheme is reachable.
    """
    result = run([
        "kubectl", "-n", MON_NS, "exec", MON_CURL, "--",
        "curl", "-s", "-S", "-k", "-o", "/dev/null",
        "-w", "%{http_code}", "--max-time", "5",
        f"{scheme}://{service}.{MON_NS}.svc:9200/",
    ])
    code = (result.stdout or "").strip()
    return result.returncode == 0 and code.isdigit() and code != "000"


def detect_scheme(service):
    """Detect a service's live HTTP scheme (http default first, then https)."""
    if service in _SCHEME:
        return _SCHEME[service]
    for scheme in (DEFAULT_SCHEME, "https" if DEFAULT_SCHEME == "http" else "http"):
        if _probe_scheme(service, scheme):
            _SCHEME[service] = scheme
            return scheme
    _SCHEME[service] = DEFAULT_SCHEME
    return DEFAULT_SCHEME


def curl_json(service, path, errors):
    scheme = detect_scheme(service)
    result = run(
        [
            "kubectl",
            "-n",
            MON_NS,
            "exec",
            MON_CURL,
            "--",
            "curl",
            "-s",
            "-S",
            "-k",
            "--max-time",
            "10",
            f"{scheme}://{service}.{MON_NS}.svc:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to query monitoring cluster {path}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for monitoring {path}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from monitoring {path}")
        return None


def check_sidecars(errors):
    result = run(
        [
            "kubectl",
            "-n",
            ES_NS,
            "get",
            "pods",
            "-l",
            f"app={ES_APP_LABEL}",
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to list Elasticsearch pods: {detail}")
        return
    pods = json.loads(result.stdout).get("items", [])
    if not pods:
        errors.append("No Elasticsearch pods found")
        return

    for pod in pods:
        names = {c.get("name") for c in pod.get("spec", {}).get("containers", [])}
        if "metricbeat" not in names or "filebeat" not in names:
            errors.append("Missing metricbeat or filebeat sidecar in Elasticsearch pods")
            return


def check_monitoring_indices(errors):
    indices = curl_json(MON_SERVICE, "/_cat/indices?format=json", errors)
    if not isinstance(indices, list):
        return
    monitoring = [i for i in indices if i.get("index", "").startswith(".monitoring-es")]
    if not monitoring:
        errors.append("Monitoring indices not found in monitoring cluster")
        return

    # Ensure at least one monitoring index has documents.
    for index in monitoring:
        count = curl_json(MON_SERVICE, f"/{index['index']}/_count", errors)
        if isinstance(count, dict) and isinstance(count.get("count"), int):
            if count["count"] > 0:
                return
    errors.append("Monitoring indices exist but have no documents")


def main():
    errors = []

    check_sidecars(errors)
    check_monitoring_indices(errors)

    if errors:
        print("Stack monitoring sidecars verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Stack monitoring sidecars verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
