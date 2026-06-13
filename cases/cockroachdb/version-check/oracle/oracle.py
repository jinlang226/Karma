#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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
        "crdb-version-report",
        "-o",
        "jsonpath={.data.db_version}",
    ]
    cm_result = run(cm_cmd)
    if cm_result.returncode != 0:
        errors.append("Missing ConfigMap crdb-version-report")
        errors.append(f"Error: {cm_result.stderr.strip()}")
        cm_version = ""
    else:
        cm_version = cm_result.stdout.strip()
        if not cm_version:
            errors.append("ConfigMap db_version is empty")

    cluster_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        "--insecure",
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
        if "23.2" not in cluster_version:
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
        "--insecure",
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
        "--insecure",
        "--format=tsv",
        "-e",
        "SELECT version();",
    ]
    binary_result = run(binary_cmd)
    if binary_result.returncode != 0:
        errors.append("Failed to read binary version")
        errors.append(f"Error: {binary_result.stderr.strip()}")
    else:
        if "v24.1" not in binary_result.stdout and "24.1" not in binary_result.stdout:
            errors.append("Binary version does not look like v24.1")

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
        elif any("cockroachdb/cockroach:v24.1.0" not in image for image in images):
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
