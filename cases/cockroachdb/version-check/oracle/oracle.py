#!/usr/bin/env python3
# Verify the agent recorded the active feature version in the configured
# ConfigMap and that the cluster is in the expected upgraded-but-pinned state.
# The ConfigMap name/key (BENCH_PARAM_REPORT_CONFIGMAP_NAME / _REPORT_KEY), the
# pre-upgrade logical version (BENCH_PARAM_FROM_VERSION) and the binary/image
# version (BENCH_PARAM_TO_VERSION) all come from the case params, so a workflow
# that overrides them is honored. Standalone (default params) this behaves
# identically to the old hardcoded check.
import os
import subprocess
import sys


REPORT_CM = os.environ.get("BENCH_PARAM_REPORT_CONFIGMAP_NAME", "crdb-version-report")
REPORT_KEY = os.environ.get("BENCH_PARAM_REPORT_KEY", "db_version")
FROM_VERSION = os.environ.get("BENCH_PARAM_FROM_VERSION", "23.2")
TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "24.1.0")
# Logical major.minor of the binary (e.g. "24.1" for "24.1.0").
TO_MAJOR_MINOR = ".".join(TO_VERSION.split(".")[:2])


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_CONN_FLAG = None


def conn_flag():
    """Return the right cockroach SQL connection flag for the live cluster.

    Standalone this case runs against an INSECURE cluster (`--insecure`). But in
    a workflow this stage can inherit a SECURE cluster left running by a prior
    stage (e.g. certificate-rotation), whose precondition probe sees pods already
    Running and skips its own insecure redeploy. A hardcoded `--insecure` then
    fails with an SSL authentication error. Detect the mode once by checking for
    the mounted certs dir and connect accordingly so the same oracle works in
    both contexts. Mirrors cockroachdb/cluster-settings/oracle/oracle.py.
    """
    global _CONN_FLAG
    if _CONN_FLAG is not None:
        return _CONN_FLAG
    probe = run([
        "kubectl", "-n", "cockroachdb", "--request-timeout=15s", "exec",
        "crdb-cluster-0", "--", "ls", "/cockroach/cockroach-certs/ca.crt",
    ])
    if probe.returncode == 0:
        _CONN_FLAG = "--certs-dir=/cockroach/cockroach-certs"
    else:
        _CONN_FLAG = "--insecure"
    return _CONN_FLAG


def tsv_last_value(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return lines[-1].split("\t")[-1]


def main():
    errors = []

    cm_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "configmap",
        REPORT_CM,
        "-o",
        "jsonpath={.data." + REPORT_KEY.replace(".", "\\.") + "}",
    ]
    cm_result = run(cm_cmd)
    if cm_result.returncode != 0:
        errors.append(f"Missing ConfigMap {REPORT_CM}")
        errors.append(f"Error: {cm_result.stderr.strip()}")
        cm_version = ""
    else:
        cm_version = cm_result.stdout.strip()
        if not cm_version:
            errors.append(f"ConfigMap {REPORT_KEY} is empty")

    cluster_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        conn_flag(),
        "--format=tsv",
        "-e",
        "SHOW CLUSTER SETTING version;",
    ]
    cluster_result = run(cluster_cmd)
    cluster_version = ""
    if cluster_result.returncode != 0:
        errors.append("Failed to read cluster version")
        errors.append(f"Error: {cluster_result.stderr.strip()}")
    else:
        cluster_version = tsv_last_value(cluster_result.stdout)
        if FROM_VERSION not in cluster_version:
            errors.append(f"Cluster version mismatch: {cluster_version or 'empty'}")

    preserve_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        conn_flag(),
        "--format=tsv",
        "-e",
        "SHOW CLUSTER SETTING cluster.preserve_downgrade_option;",
    ]
    preserve_result = run(preserve_cmd)
    if preserve_result.returncode != 0:
        errors.append("Failed to read preserve_downgrade_option")
        errors.append(f"Error: {preserve_result.stderr.strip()}")
    else:
        preserve_value = tsv_last_value(preserve_result.stdout)
        if not preserve_value:
            errors.append("preserve_downgrade_option is empty")

    binary_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        conn_flag(),
        "--format=tsv",
        "-e",
        "SELECT version();",
    ]
    binary_result = run(binary_cmd)
    if binary_result.returncode != 0:
        errors.append("Failed to read binary version")
        errors.append(f"Error: {binary_result.stderr.strip()}")
    else:
        if f"v{TO_MAJOR_MINOR}" not in binary_result.stdout and TO_MAJOR_MINOR not in binary_result.stdout:
            errors.append(f"Binary version does not look like v{TO_MAJOR_MINOR}")

    images_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=cockroachdb",
        "-o",
        "jsonpath={.items[*].spec.containers[0].image}",
    ]
    images_result = run(images_cmd)
    if images_result.returncode != 0:
        errors.append("Failed to read pod images")
        errors.append(f"Error: {images_result.stderr.strip()}")
    else:
        images = [image.strip() for image in images_result.stdout.split() if image.strip()]
        if not images:
            errors.append("No pod images reported")
        elif any(f"cockroachdb/cockroach:v{TO_VERSION}" not in image for image in images):
            errors.append(f"Unexpected pod images: {' '.join(images)}")

    if cm_version and cluster_version and cm_version != cluster_version:
        errors.append(f"ConfigMap db_version does not match cluster version ({cluster_version})")

    if errors:
        print("Version check verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Version check completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
