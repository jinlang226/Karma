#!/usr/bin/env python3
"""Oracle for cockroachdb/cluster-settings.

Verifies the agent set the configured cluster setting to the configured value and
that it persists across a pod restart. Both the setting name and the target value
come from the case params (BENCH_PARAM_SETTING_NAME / BENCH_PARAM_SETTING_VALUE),
so the same oracle works for every setting the workflows exercise (byte rates,
booleans, durations, ...). This matters for the workflow regression sweep: a later
stage that reverts this setting must be detected, which requires checking the
*configured* setting rather than a single hardcoded one.
"""
import os
import re
import subprocess
import sys
import time


# The precondition always stands the cluster up in the literal "cockroachdb"
# namespace (every apply/exec hardcodes `-n cockroachdb`), so the oracle must
# look there too. Trusting BENCH_NAMESPACE here pointed the oracle at the
# role-bound ephemeral namespace where crdb-cluster-0 does not exist, making
# every check fail with "pods crdb-cluster-0 not found". The sibling cockroachdb
# oracles (deploy/initialize/health-check-recovery/version-check) all hardcode
# "cockroachdb" for the same reason.
NAMESPACE = "cockroachdb"
POD = "crdb-cluster-0"
SETTING_NAME = os.environ.get("BENCH_PARAM_SETTING_NAME", "kv.snapshot_rebalance.max_rate")
SETTING_VALUE = os.environ.get("BENCH_PARAM_SETTING_VALUE", "128MiB")


def run(cmd, timeout=30):
    """Run a kubectl command, bounding it so a stalled exec/wait cannot hang.

    A `kubectl exec ./cockroach sql` opened right after a pod delete can stall on
    a half-open API-server connection and never return; with no per-call timeout
    that blocks the whole oracle until the outer cap. Killing a stalled call and
    letting the caller's retry loop reissue it (a fresh connection succeeds)
    keeps the oracle well within its time budget.
    """
    try:
        return subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")


_BYTE_UNITS = {
    "b": 1, "kb": 1000, "kib": 1024, "mb": 1000 ** 2, "mib": 1024 ** 2,
    "gb": 1000 ** 3, "gib": 1024 ** 3, "tb": 1000 ** 4, "tib": 1024 ** 4,
}


def normalize(value):
    """Classify a setting value into a comparable (kind, value) pair.

    Handles byte quantities (MiB/GB/...), booleans, durations (1m30s / 90s /
    HH:MM:SS), plain numbers, and falls back to a lowercased string.
    """
    raw = (value or "").strip()
    low = raw.lower().replace(" ", "")
    if low in ("true", "t", "on", "yes"):
        return ("bool", True)
    if low in ("false", "f", "off", "no"):
        return ("bool", False)
    bytes_ = low.replace("/s", "")
    for suffix in sorted(_BYTE_UNITS, key=len, reverse=True):
        if bytes_.endswith(suffix):
            num = bytes_[: -len(suffix)]
            try:
                return ("bytes", int(float(num) * _BYTE_UNITS[suffix]))
            except ValueError:
                break
    secs = _parse_duration(low)
    if secs is not None:
        return ("dur", secs)
    try:
        return ("num", float(low))
    except ValueError:
        return ("str", low)


def _parse_duration(s):
    """Parse a CockroachDB duration (e.g. 1m30s, 90s, 3s, 00:01:30) to seconds."""
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", s)
    if m:
        h, mn, sec = (int(g or 0) for g in m.groups())
        return float(h * 3600 + mn * 60 + sec)
    m = re.fullmatch(
        r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?",
        s,
    )
    if m and any(m.groups()):
        h, mn, sec, ms = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mn * 60 + sec + ms / 1000.0
    return None


def values_match(expected, actual):
    """True if the live setting value equals the configured target value."""
    ek, ev = normalize(expected)
    ak, av = normalize(actual)
    if ek == ak:
        return ev == av
    # Mixed classification (e.g. numeric vs byte) — compare canonical strings.
    return str(ev).lower() == str(av).lower()


def get_setting():
    """Read the live value of the configured cluster setting (last tsv line)."""
    cmd = [
        "kubectl", "-n", NAMESPACE, "--request-timeout=20s", "exec", POD, "--",
        "./cockroach", "sql", "--insecure", "--format=tsv",
        "-e", f"SHOW CLUSTER SETTING {SETTING_NAME};",
    ]
    result = run(cmd, timeout=25)
    if result.returncode != 0:
        return None, result.stderr.strip() or "SHOW CLUSTER SETTING failed"
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None, "Empty setting output"
    return lines[-1], None


def check(errors, phase):
    """Read the setting and record an error if it doesn't match the target.

    A freshly restarted node can report Ready to Kubernetes a moment before
    CockroachDB itself accepts SQL clients ("server is not accepting clients",
    SQLSTATE 57P01), so retry the read briefly before treating it as a failure.
    """
    actual = err = None
    # Bound by wall-clock, not attempt count: each get_setting is itself capped
    # (see run()), so a stalled exec is killed and reissued on a fresh
    # connection rather than blocking the oracle indefinitely.
    deadline = time.monotonic() + 45
    while True:
        actual, err = get_setting()
        if not err or time.monotonic() >= deadline:
            break
        time.sleep(3)
    if err:
        errors.append(f"Failed to read {SETTING_NAME} {phase}: {err}")
        return
    if not values_match(SETTING_VALUE, actual):
        errors.append(
            f"{SETTING_NAME} {phase} is '{actual}', expected '{SETTING_VALUE}'"
        )


def wait_pod_ready(deadline_sec=90):
    """Wait for POD to exist AND become Ready, tolerating the recreation window.

    The agent and this oracle both restart crdb-cluster-0 to test persistence,
    so the pod is frequently mid-recreate (NotFound, then Pending) when called.
    `kubectl wait` returns immediately with an error when the pod object does
    not yet exist, so this polls until the pod reappears and reports Ready,
    rather than failing on the first NotFound. Returns (ok, last_error).
    """
    start = time.monotonic()
    last_err = "pod did not become ready"
    while time.monotonic() - start < deadline_sec:
        wait = run([
            "kubectl", "-n", NAMESPACE, "--request-timeout=30s", "wait",
            "--for=condition=ready", f"pod/{POD}", "--timeout=30s",
        ], timeout=35)
        if wait.returncode == 0:
            return True, None
        last_err = (wait.stderr or wait.stdout or last_err).strip()
        time.sleep(3)
    return False, last_err


def main():
    """Verify the configured setting matches the target before and after restart."""
    errors = []

    # The agent may have just restarted crdb-cluster-0 to test persistence
    # itself, leaving it briefly absent/not-ready. Wait for it to settle before
    # the first read so a transient restart window isn't mistaken for a failure.
    ok, err = wait_pod_ready()
    if not ok:
        errors.append(f"Pod not ready before persistence check: {err}")

    check(errors, "before restart")

    delete = run(["kubectl", "-n", NAMESPACE, "--request-timeout=30s", "delete", "pod", POD], timeout=40)
    if delete.returncode != 0:
        errors.append(f"Failed to delete pod for persistence check: {delete.stderr.strip()}")
    # After the delete the StatefulSet recreates the pod; wait for it to exist
    # and become Ready again, tolerating the brief NotFound recreation window.
    ok, err = wait_pod_ready()
    if not ok:
        errors.append(f"Pod did not become ready after restart: {err}")

    check(errors, "after restart")

    if errors:
        print("Cluster settings verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Cluster setting {SETTING_NAME} = {SETTING_VALUE} verified (persists across restart)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
