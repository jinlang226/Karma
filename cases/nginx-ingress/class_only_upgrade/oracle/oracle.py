#!/usr/bin/env python3
import os
import subprocess
import sys
import time

# Param-aware: a workflow can override host/expected_body via param_overrides;
# read BENCH_PARAM_* (default = the standalone value) so the oracle checks the
# host this stage was asked to serve on the live cluster. Pass criterion
# unchanged.
HOST = os.environ.get("BENCH_PARAM_HOST") or "class.example.com"
EXPECTED_BODY = os.environ.get("BENCH_PARAM_EXPECTED_BODY") or "hello"

# Reachability is transient-prone: once the agent assigns the IngressClass the
# selected controller reloads asynchronously and the gateway Service may still be
# warming up. A single curl races that, so re-evaluate within a bounded window
# and pass as soon as the body matches. This does not loosen the criterion -- a
# host that never serves the expected body still fails after the deadline.
DEADLINE_SEC = 120
INTERVAL_SEC = 3


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    cmd = [
        "kubectl",
        "-n",
        "demo",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "-H",
        f"Host: {HOST}",
        "http://ingress-gateway.demo.svc.cluster.local/",
    ]
    deadline = time.monotonic() + DEADLINE_SEC
    last_err = "no response"
    while True:
        result = run(cmd)
        if result.returncode != 0:
            last_err = result.stderr.strip() or "ingress request failed"
        else:
            body = result.stdout.strip()
            if body == EXPECTED_BODY:
                return 0
            last_err = f"unexpected response body: {body}"
        if time.monotonic() >= deadline:
            break
        time.sleep(INTERVAL_SEC)

    print(f"Ingress request failed: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
