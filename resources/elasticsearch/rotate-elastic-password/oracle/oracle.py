#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CURL_POD = "curl-test"
SECRET_CURRENT = os.environ.get("BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME", "elastic-password")
SECRET_NEXT = os.environ.get("BENCH_PARAM_NEXT_PASSWORD_SECRET_NAME", "elastic-password-next")
CONFIG_OLD = os.environ.get("BENCH_PARAM_PREVIOUS_PASSWORD_CONFIGMAP_NAME", "elastic-password-prev")
AUTH_DEPLOY = os.environ.get("BENCH_PARAM_AUTH_CHECKER_DEPLOYMENT_NAME", "auth-checker")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def get_secret_value(name, key="password"):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "secret",
            name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()
    try:
        return base64.b64decode(result.stdout.strip()).decode("utf-8"), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def get_config_value(name, key="password"):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip()
    return result.stdout.strip(), None


def curl_auth_with_code(password, path):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CURL_POD,
            "--",
            "curl",
            "-s",
            "-S",
            "--max-time",
            "10",
            "-u",
            f"elastic:{password}",
            "-w",
            "\\n%{http_code}",
            f"http://{SERVICE}:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return None, None, detail
    output = result.stdout
    if not output or "\n" not in output:
        return None, None, f"unexpected response: {output!r}"
    body, code = output.rsplit("\n", 1)
    return body, code.strip(), None


def check_auth_checker(errors):
    result = run(["kubectl", "-n", NAMESPACE, "get", "deploy", AUTH_DEPLOY, "-o", "json"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read auth checker deployment: {detail}")
        return
    deploy = json.loads(result.stdout)
    available = deploy.get("status", {}).get("availableReplicas") or 0
    if available < 1:
        errors.append("auth-checker is not Ready")


def main():
    errors = []

    current, err = get_secret_value(SECRET_CURRENT)
    if err:
        errors.append(f"Unable to read {SECRET_CURRENT}: {err}")
    new_pw, err = get_secret_value(SECRET_NEXT)
    if err:
        errors.append(f"Unable to read {SECRET_NEXT}: {err}")
    old_pw, err = get_config_value(CONFIG_OLD)
    if err:
        errors.append(f"Unable to read {CONFIG_OLD}: {err}")

    if current and new_pw and current != new_pw:
        errors.append("Active password secret does not match target password secret")

    if new_pw:
        body, code, err = curl_auth_with_code(new_pw, "/_security/_authenticate")
        if err:
            errors.append(f"New password auth failed: {err}")
        elif code != "200":
            errors.append(f"New password auth returned status {code}")
        else:
            try:
                payload = json.loads(body.strip())
            except json.JSONDecodeError:
                errors.append("New password auth returned invalid JSON")
            else:
                if payload.get("username") != "elastic":
                    errors.append("New password auth did not return elastic user")

    if old_pw:
        _, code, err = curl_auth_with_code(old_pw, "/_security/_authenticate")
        if err:
            errors.append(f"Old password auth check failed: {err}")
        elif code != "401":
            errors.append("Old password still works")

    check_auth_checker(errors)

    if errors:
        print("Rotate elastic password verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Rotate elastic password verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
