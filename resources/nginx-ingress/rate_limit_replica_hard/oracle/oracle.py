#!/usr/bin/env python3
import subprocess
import sys


REQUEST_COUNT = 10
MIN_429 = 4
HOST = "rate.example.com"
SERVICE_URL = "http://ingress-nginx-controller.ingress-nginx.svc"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def paced(path):
    loop = " ".join(str(i) for i in range(1, REQUEST_COUNT + 1))
    shell_cmd = (
        "for i in "
        + loop
        + "; do curl -s -o /dev/null -w '%{http_code}\\n' -H 'Host: "
        + HOST
        + "' "
        + SERVICE_URL
        + path
        + "; sleep 0.5; done"
    )
    cmd = ["kubectl", "-n", "demo", "exec", "curl-test", "--", "sh", "-c", shell_cmd]
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "paced command failed")
    codes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return codes


def main():
    try:
        api_codes = paced("/api")
    except Exception as exc:
        print(f"API test failed: {exc}", file=sys.stderr)
        return 1

    try:
        health_codes = paced("/health")
    except Exception as exc:
        print(f"Health test failed: {exc}", file=sys.stderr)
        return 1

    api_200 = api_codes.count("200")
    api_429 = api_codes.count("429")
    api_other = [code for code in api_codes if code not in ("200", "429")]

    if api_429 < MIN_429:
        print(
            f"/api returned too few 429 responses ({api_429}/{REQUEST_COUNT}): {api_codes}",
            file=sys.stderr,
        )
        return 1
    if api_200 < 1:
        print(f"/api did not return any 200 responses: {api_codes}", file=sys.stderr)
        return 1
    if api_other:
        print(f"/api returned unexpected codes: {api_other}", file=sys.stderr)
        return 1

    health_other = [code for code in health_codes if code != "200"]
    if health_other:
        print(f"/health returned non-200 codes: {health_other}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
