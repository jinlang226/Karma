#!/usr/bin/env python3
import base64
import json
import subprocess
import sys

NAMESPACE = "elasticsearch"
SERVICE = "es-http"
SECRET = "es-file-realm-aggregate"
OPS_USER = "ops-user"
OPS_PASS = "opspass"
REPORT_USER = "report-user"
REPORT_PASS = "reportpass"
INDEX = "app-data"


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
        f"https://{SERVICE}.{NAMESPACE}.svc:9200{path}",
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
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "secret",
            SECRET,
            "-o",
            "json",
        ]
    )
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
    except Exception as exc:
        errors.append(f"Failed to decode {key} in {SECRET} secret: {exc}")
        return ""


def parse_users(text):
    users = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 1)
        if parts:
            users.add(parts[0].strip())
    return users


def parse_users_roles(text):
    users = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 1)
        if len(parts) != 2:
            continue
        for user in parts[1].split(","):
            user = user.strip()
            if user:
                users.add(user)
    return users


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
    users = parse_users(users_text)
    missing_users = sorted(expected - users)
    if missing_users:
        errors.append(f"Missing users in {SECRET}: {', '.join(missing_users)}")

    users_roles = parse_users_roles(users_roles_text)
    missing_roles = sorted(expected - users_roles)
    if missing_roles:
        errors.append(
            f"Missing users in {SECRET} users_roles: {', '.join(missing_roles)}"
        )

    health = curl_json("/_cluster/health", OPS_USER, OPS_PASS, errors)
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")

    count = curl_json(f"/{INDEX}/_count", REPORT_USER, REPORT_PASS, errors)
    if isinstance(count, dict):
        if "count" not in count:
            errors.append("report-user failed to read app-data count")

    if errors:
        print_errors(errors)
        return 1

    print("File realm merge verified")
    return 0


def print_errors(errors):
    print("File realm merge verification failed:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
