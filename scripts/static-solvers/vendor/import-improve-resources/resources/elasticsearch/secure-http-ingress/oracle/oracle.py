#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
ES_SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
INGRESS_NAMESPACE = os.environ.get("BENCH_NS_INGRESS", "ingress-nginx")
INGRESS_SERVICE_NAME = os.environ.get("BENCH_PARAM_INGRESS_SERVICE_NAME", "ingress-nginx-controller")
INGRESS_HOST = os.environ.get("BENCH_PARAM_INGRESS_HOST", "es.example.com")
INGRESS_CLASS_NAME = os.environ.get("BENCH_PARAM_INGRESS_CLASS_NAME", "nginx")
TLS_SECRET_NAME = os.environ.get("BENCH_PARAM_TLS_SECRET_NAME", "es-http-tls")
ELASTIC_USER = "elastic"
ELASTIC_PASS = "elasticpass"


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
        "3",
        "--max-time",
        "8",
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


def curl_json_retry(args, errors, label, timeout_sec=90):
    deadline = time.monotonic() + timeout_sec
    last_errors = []
    while True:
        attempt_errors = []
        payload = curl_json(args, attempt_errors, label)
        if isinstance(payload, (dict, list)):
            return payload
        last_errors = attempt_errors
        if time.monotonic() >= deadline:
            errors.extend(last_errors)
            return None
        time.sleep(3)


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
        "3",
        "--max-time",
        "8",
    ] + args
    result = run(cmd)
    return result.returncode, result.stdout.strip()


def main():
    errors = []
    ingress_service = f"{INGRESS_SERVICE_NAME}.{INGRESS_NAMESPACE}.svc"
    ingress_obj = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "ingress",
            "-o",
            "json",
        ]
    )
    if ingress_obj.returncode != 0:
        detail = ingress_obj.stderr.strip() or ingress_obj.stdout.strip() or f"exit {ingress_obj.returncode}"
        errors.append(f"Failed to list Ingress resources: {detail}")
        ingresses = []
    else:
        try:
            ingresses = json.loads(ingress_obj.stdout).get("items", [])
        except json.JSONDecodeError:
            errors.append("Failed to parse Ingress resources")
            ingresses = []

    secret_obj = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "secret",
            TLS_SECRET_NAME,
            "-o",
            "name",
        ]
    )
    if secret_obj.returncode != 0:
        detail = secret_obj.stderr.strip() or secret_obj.stdout.strip() or f"exit {secret_obj.returncode}"
        errors.append(f"Failed to read TLS secret {TLS_SECRET_NAME}: {detail}")

    ingress = None
    for candidate in ingresses:
        spec = candidate.get("spec", {}) or {}
        rules = spec.get("rules") or []
        if not any(item.get("host") == INGRESS_HOST for item in rules if isinstance(item, dict)):
            continue
        ingress = candidate
        break

    if isinstance(ingress, dict):
        spec = ingress.get("spec", {})
        if spec.get("ingressClassName") != INGRESS_CLASS_NAME:
            errors.append(
                f"Ingress class mismatch: expected {INGRESS_CLASS_NAME}, got {spec.get('ingressClassName')}"
            )
        tls_entries = spec.get("tls") or []
        if not any(item.get("secretName") == TLS_SECRET_NAME for item in tls_entries if isinstance(item, dict)):
            errors.append(f"Ingress does not reference TLS secret {TLS_SECRET_NAME}")
        rules = spec.get("rules") or []
        if not any(item.get("host") == INGRESS_HOST for item in rules if isinstance(item, dict)):
            errors.append(f"Ingress does not expose host {INGRESS_HOST}")
    else:
        errors.append(f"No Ingress exposes host {INGRESS_HOST}")

    es_health = curl_json(
        [
            "-k",
            "-u",
            f"{ELASTIC_USER}:{ELASTIC_PASS}",
            f"https://{ES_SERVICE}:9200/_cluster/health?wait_for_status=yellow&timeout=5s",
        ],
        errors,
        "Elasticsearch HTTPS",
    )
    if isinstance(es_health, dict):
        if es_health.get("status") not in {"yellow", "green"}:
            errors.append(f"Elasticsearch HTTPS health not yellow/green: {es_health.get('status')}")

    ingress_health = curl_json_retry(
        [
            "-k",
            "-u",
            f"{ELASTIC_USER}:{ELASTIC_PASS}",
            "-H",
            f"Host: {INGRESS_HOST}",
            f"https://{ingress_service}/_cluster/health?wait_for_status=yellow&timeout=5s",
        ],
        errors,
        "Ingress HTTPS",
    )
    if isinstance(ingress_health, dict):
        if ingress_health.get("status") not in {"yellow", "green"}:
            errors.append(f"Ingress HTTPS health not yellow/green: {ingress_health.get('status')}")

    rc, code = curl_http_code(["-u", f"{ELASTIC_USER}:{ELASTIC_PASS}", f"http://{ES_SERVICE}:9200/_cluster/health"])
    if rc == 0 and code == "200":
        errors.append("Elasticsearch HTTP still succeeds")

    rc, code = curl_http_code(
        [
            "-u",
            f"{ELASTIC_USER}:{ELASTIC_PASS}",
            "-H",
            f"Host: {INGRESS_HOST}",
            f"http://{ingress_service}/_cluster/health",
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
