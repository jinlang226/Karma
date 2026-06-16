#!/usr/bin/env python3
import json
import subprocess
import sys

NAMESPACE = "elasticsearch"
ES_SERVICE = "es-http"
INGRESS_SERVICE = "ingress-nginx-controller.ingress-nginx.svc"
INGRESS_HOST = "es.example.com"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(args, errors, label):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "--connect-timeout",
        "2",
        "--max-time",
        "5",
    ] + args
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to query {label}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {label}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Unable to parse JSON for {label}")
        return None


def curl_http_code(args):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--connect-timeout",
        "2",
        "--max-time",
        "5",
    ] + args
    result = run(cmd)
    return result.returncode, result.stdout.strip()


def main():
    errors = []

    # The backend Elasticsearch serves plain HTTP on 9200 standalone (the
    # precondition sets xpack.security.http.ssl.enabled: false) -- the task is to
    # terminate TLS at the ingress, not on the ES service itself. Use this as an
    # "ES is up" sanity gate. Because the env PERSISTS across stages, a prior
    # stage may have enabled HTTP TLS on the backend, so detect the live scheme
    # (http first, then https) rather than assuming plain http.
    es_scheme = "http"
    for _scheme in ("http", "https"):
        _rc, _code = curl_http_code(
            ["-k", f"{_scheme}://{ES_SERVICE}.{NAMESPACE}.svc:9200/"]
        )
        if _rc == 0 and _code.isdigit() and _code != "000":
            es_scheme = _scheme
            break
    es_health = curl_json(
        ["-k", f"{es_scheme}://{ES_SERVICE}.{NAMESPACE}.svc:9200/_cluster/health"],
        errors,
        "Elasticsearch backend",
    )
    if isinstance(es_health, dict):
        if es_health.get("status") not in {"yellow", "green"}:
            errors.append(f"Elasticsearch health not yellow/green: {es_health.get('status')}")

    ingress_health = curl_json(
        [
            "-k",
            "-H",
            f"Host: {INGRESS_HOST}",
            f"https://{INGRESS_SERVICE}/_cluster/health",
        ],
        errors,
        "Ingress HTTPS",
    )
    if isinstance(ingress_health, dict):
        if ingress_health.get("status") not in {"yellow", "green"}:
            errors.append(
                f"Ingress HTTPS health not yellow/green: {ingress_health.get('status')}"
            )

    # NOTE: we intentionally do NOT assert that direct http://es-http:9200 fails.
    # The backend is plain HTTP by design and only reachable in-cluster; the task
    # is to block plain HTTP *at the ingress*, which is what the next check covers.
    rc, code = curl_http_code(
        [
            "-H",
            f"Host: {INGRESS_HOST}",
            f"http://{INGRESS_SERVICE}/_cluster/health",
        ]
    )
    if rc == 0 and code == "200":
        errors.append("Ingress HTTP still succeeds")

    if errors:
        print("Secure HTTP ingress verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Secure HTTP ingress verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
