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


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "cockroachdb")
POD = "crdb-cluster-0"
SETTING_NAME = os.environ.get("BENCH_PARAM_SETTING_NAME", "kv.snapshot_rebalance.max_rate")
SETTING_VALUE = os.environ.get("BENCH_PARAM_SETTING_VALUE", "128MiB")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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
        "kubectl", "-n", NAMESPACE, "exec", POD, "--",
        "./cockroach", "sql", "--insecure", "--format=tsv",
        "-e", f"SHOW CLUSTER SETTING {SETTING_NAME};",
    ]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip() or "SHOW CLUSTER SETTING failed"
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None, "Empty setting output"
    return lines[-1], None


def check(errors, phase):
    """Read the setting and record an error if it doesn't match the target."""
    actual, err = get_setting()
    if err:
        errors.append(f"Failed to read {SETTING_NAME} {phase}: {err}")
        return
    if not values_match(SETTING_VALUE, actual):
        errors.append(
            f"{SETTING_NAME} {phase} is '{actual}', expected '{SETTING_VALUE}'"
        )


def main():
    """Verify the configured setting matches the target before and after restart."""
    errors = []

    check(errors, "before restart")

    delete = run(["kubectl", "-n", NAMESPACE, "delete", "pod", POD])
    if delete.returncode != 0:
        errors.append(f"Failed to delete pod for persistence check: {delete.stderr.strip()}")
    wait = run([
        "kubectl", "-n", NAMESPACE, "wait", "--for=condition=ready",
        f"pod/{POD}", "--timeout=120s",
    ])
    if wait.returncode != 0:
        errors.append(f"Pod did not become ready after restart: {wait.stderr.strip()}")

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
