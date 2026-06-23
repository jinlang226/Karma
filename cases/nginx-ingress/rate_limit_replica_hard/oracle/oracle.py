#!/usr/bin/env python3
import os
import subprocess
import sys
import time


REQUEST_COUNT = 20
MIN_429 = 4
# Transient-prone reachability: in a workflow the controller / curl-test pod may
# be warming up when the oracle runs, so the burst exec itself can fail before it
# ever measures rate limiting. Retry only that INFRASTRUCTURE failure (the exec
# erroring out) within a bounded window; the rate-limit verdict on a successful
# burst is unchanged and single-shot, so a path that is genuinely not limited
# still fails.
PROBE_DEADLINE_SEC = 120
PROBE_INTERVAL_SEC = 3
# Param-aware: a workflow can override host/api_path/health_path via
# param_overrides; read BENCH_PARAM_* (default = the standalone value) so the
# oracle exercises the routes this stage configured on the live cluster. The
# rate-limit pass criterion (>= MIN_429 429s on api, only 200s on health) is
# unchanged.
HOST = os.environ.get("BENCH_PARAM_HOST") or "rate.example.com"
API_PATH = os.environ.get("BENCH_PARAM_API_PATH") or "/api"
HEALTH_PATH = os.environ.get("BENCH_PARAM_HEALTH_PATH") or "/health"
SERVICE_URL = "http://ingress-nginx-controller.ingress-nginx.svc"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def paced(path):
    # Fire the requests as a FAST BURST (no inter-request sleep) -- excess traffic
    # relative to ANY finite limit, so the probe is workflow-agnostic. The old
    # version paced at a hardcoded ~2 rps (sleep 0.5); that only generated 429s
    # when limit_rps < 2, so a workflow overriding limit_rps=2 (the leaky bucket
    # refills exactly as fast as the probe sends) saw zero 429s and could never
    # pass -- even though rate limiting WAS correctly configured (a burst, which
    # the agent itself verifies with, returns 429 for excess at any rate). A burst
    # trips the limit regardless of its configured value; an UNlimited path still
    # returns all 200, so the check stays sound.
    loop = " ".join(str(i) for i in range(1, REQUEST_COUNT + 1))
    shell_cmd = (
        "for i in "
        + loop
        + "; do curl -s -o /dev/null -w '%{http_code}\\n' -H 'Host: "
        + HOST
        + "' "
        + SERVICE_URL
        + path
        + "; done"
    )
    cmd = ["kubectl", "-n", "demo", "exec", "curl-test", "--", "sh", "-c", shell_cmd]
    result = run(cmd)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "paced command failed")
    codes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return codes


def paced_with_retry(path, label):
    # Re-issue the burst only when the exec itself errors (transient warm-up),
    # bounded by PROBE_DEADLINE_SEC. Returns the response codes once the exec
    # succeeds, or raises the last error after the deadline.
    deadline = time.monotonic() + PROBE_DEADLINE_SEC
    while True:
        try:
            return paced(path)
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise
            print(f"{label} test transiently failed, retrying: {exc}", file=sys.stderr)
            time.sleep(PROBE_INTERVAL_SEC)


def main():
    try:
        api_codes = paced_with_retry(API_PATH, "API")
    except Exception as exc:
        print(f"API test failed: {exc}", file=sys.stderr)
        return 1

    try:
        health_codes = paced_with_retry(HEALTH_PATH, "Health")
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
