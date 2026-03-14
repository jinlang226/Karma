#!/usr/bin/env python3
import json
import os
import subprocess
import sys

ES_NS = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
MON_NS = os.environ.get("BENCH_NS_MONITORING") or ES_NS
MON_SERVICE = os.environ.get("BENCH_PARAM_MONITORING_SERVICE_NAME", "monitoring-es-http")
MON_CURL = os.environ.get("BENCH_PARAM_MONITORING_CURL_POD_NAME", "monitoring-curl-test")
ES_APP_LABEL = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(service, path, errors):
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
            "--max-time",
            "10",
            f"http://{service}:9200{path}",
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
