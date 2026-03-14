#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
SECRET = os.environ.get("BENCH_PARAM_AGGREGATE_SECRET_NAME", "es-file-realm-aggregate")
OPS_USER = os.environ.get("BENCH_PARAM_OPS_USER", "ops-user")
OPS_PASS = os.environ.get("BENCH_PARAM_OPS_PASSWORD", "opspass")
REPORT_USER = os.environ.get("BENCH_PARAM_REPORT_USER", "report-user")
REPORT_PASS = os.environ.get("BENCH_PARAM_REPORT_PASSWORD", "reportpass")
INDEX = os.environ.get("BENCH_PARAM_SEED_INDEX_NAME", "app-data")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path, user, password, errors):
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
        "-k",
        "-u",
        f"{user}:{password}",
        f"https://{SERVICE}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"command terminated with exit code {result.returncode}"
        errors.append(f"Failed to query {path} as {user}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {path} as {user}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from {path} as {user}")
        return None


def get_secret(errors):
    result = run(["kubectl", "-n", NAMESPACE, "get", "secret", SECRET, "-o", "json"])
    if result.returncode != 0:
        errors.append(f"Failed to read {SECRET} secret: {result.stderr.strip()}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse {SECRET} secret JSON")
        return None


def decode_field(data, key, errors):
    value = data.get(key)
    if value is None:
        errors.append(f"Missing {key} in {SECRET} secret")
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Failed to decode {key} in {SECRET} secret: {exc}")
        return ""


def parse_users(text):
    users = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        users.add(line.split(":", 1)[0].strip())
    return users


def parse_users_roles(text):
    users = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        for user in line.split(":", 1)[1].split(","):
            user = user.strip()
            if user:
                users.add(user)
    return users


def print_errors(errors):
    print("File realm merge verification failed:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)


def main():
    errors = []
    secret = get_secret(errors)
    if secret is None:
        print_errors(errors)
        return 1

    data = secret.get("data", {})
    users_text = decode_field(data, "users", errors)
    users_roles_text = decode_field(data, "users_roles", errors)

    expected = {OPS_USER, REPORT_USER}
    missing_users = sorted(expected - parse_users(users_text))
    if missing_users:
        errors.append(f"Missing users in {SECRET}: {', '.join(missing_users)}")

    missing_roles = sorted(expected - parse_users_roles(users_roles_text))
    if missing_roles:
        errors.append(f"Missing users in {SECRET} users_roles: {', '.join(missing_roles)}")

    health = curl_json("/_cluster/health", OPS_USER, OPS_PASS, errors)
    if isinstance(health, dict) and health.get("status") not in {"yellow", "green"}:
        errors.append(f"Cluster health status expected yellow/green, got {health.get('status')}")

    count = curl_json(f"/{INDEX}/_count", REPORT_USER, REPORT_PASS, errors)
    if isinstance(count, dict) and "count" not in count:
        errors.append(f"{REPORT_USER} failed to read {INDEX} count")

    if errors:
        print_errors(errors)
        return 1

    print("File realm merge verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
