#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
PROD_SERVICE = os.environ.get("BENCH_PARAM_PROD_SERVICE_NAME", "search-http")
DEV_SERVICE = os.environ.get("BENCH_PARAM_DEV_SERVICE_NAME", "search-alt")
INDEX = os.environ.get("BENCH_PARAM_INDEX_NAME", "app-logs")
MIN_COUNT = int(os.environ.get("BENCH_PARAM_MIN_DOC_COUNT", "5"))
LOG_READER_DEPLOY = os.environ.get("BENCH_PARAM_LOG_READER_DEPLOYMENT", "log-reader")
LOG_READER_IMAGE = "curlimages/curl:8.5.0"
LOG_READER_SCRIPT = """set -e
count=$(curl -s --max-time 5 \\
  http://search-http:9200/app-logs/_count \\
  | sed -n 's/.*"count":[ ]*\\([0-9]*\\).*/\\1/p')
if [ -z "$count" ]; then
  echo "log-reader: failed to parse count from search-http"
  exit 1
fi
if [ "$count" -lt 5 ]; then
  echo "log-reader: expected >=5 docs from search-http, got $count"
  exit 1
fi
echo "log-reader: ok count=$count"
sleep infinity
"""


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(service, path, errors):
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
        "--max-time",
        "10",
        f"http://{service}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to query {service}{path}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {service}{path}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from {service}{path}")
        return None


def normalize_script(script):
    return "\n".join(line.rstrip() for line in script.strip().splitlines())


def check_log_reader(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "deploy",
            LOG_READER_DEPLOY,
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read log-reader deployment: {detail}")
        return

    deploy = json.loads(result.stdout)
    spec = deploy.get("spec", {})
    template = spec.get("template", {})
    containers = template.get("spec", {}).get("containers", [])
    if not containers:
        errors.append("log-reader deployment has no containers")
        return

    container = containers[0]
    if container.get("image") != LOG_READER_IMAGE:
        errors.append("log-reader deployment was modified (image mismatch)")

    command = container.get("command") or []
    if len(command) < 3 or command[0] != "/bin/sh" or command[1] != "-c":
        errors.append("log-reader deployment was modified (command mismatch)")
    else:
        actual = normalize_script(command[2])
        expected = normalize_script(LOG_READER_SCRIPT)
        if actual != expected:
            errors.append("log-reader deployment was modified (script mismatch)")

    status = deploy.get("status", {})
    available = status.get("availableReplicas") or 0
    if available < 1:
        errors.append("log-reader is not healthy")


def main():
    errors = []

    check_log_reader(errors)

    prod = curl_json(
        PROD_SERVICE,
        "/_cluster/health?wait_for_status=yellow&timeout=30s",
        errors,
    )
    if isinstance(prod, dict):
        status = prod.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"search-http health expected yellow/green, got {status}")
        if prod.get("number_of_nodes") != 3:
            errors.append(
                f"search-http expected 3 nodes, got {prod.get('number_of_nodes')}"
            )

    count = curl_json(PROD_SERVICE, f"/{INDEX}/_count", errors)
    if isinstance(count, dict):
        if not isinstance(count.get("count"), int) or count.get("count") < MIN_COUNT:
            errors.append(f"Expected at least {MIN_COUNT} log docs, got {count.get('count')}")

    dev = curl_json(
        DEV_SERVICE,
        "/_cluster/health?wait_for_status=yellow&timeout=30s",
        errors,
    )
    if isinstance(dev, dict):
        if dev.get("number_of_nodes") != 1:
            errors.append(
                f"search-alt expected 1 node, got {dev.get('number_of_nodes')}"
            )

    if errors:
        print("Internal HTTP service drift verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Internal HTTP service drift verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
