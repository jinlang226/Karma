#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    cluster_pod,
    cluster_prefix,
    cockroach_image,
    run,
    tsv_last_value,
    version_family,
)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)

    from_version = bench_param("from_version", "23.2.0")
    to_version = bench_param("to_version", "24.1.0")
    report_configmap_name = bench_param("report_configmap_name", "crdb-version-report")
    report_key = bench_param("report_key", "db_version")

    from_version_family = version_family(from_version)
    to_version_family = version_family(to_version)
    target_image = cockroach_image(to_version)

    errors = []

    cm_cmd = [
        "kubectl",
        "-n",
        namespace,
        "get",
        "configmap",
        report_configmap_name,
        "-o",
        f"jsonpath={{.data.{report_key}}}",
    ]
    cm_result = run(cm_cmd)
    if cm_result.returncode != 0:
        errors.append(f"Missing ConfigMap {report_configmap_name}")
        errors.append(f"Error: {cm_result.stderr.strip()}")
        cm_version = ""
    else:
        cm_version = cm_result.stdout.strip()
        if not cm_version:
            errors.append(f"ConfigMap key {report_key} is empty")

    cluster_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
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
        if from_version_family not in cluster_version and str(from_version) not in cluster_version:
            errors.append(f"Cluster version mismatch: {cluster_version or 'empty'}")

    preserve_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
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
        elif from_version_family not in preserve_value and str(from_version) not in preserve_value:
            errors.append(
                f"preserve_downgrade_option mismatch: {preserve_value}"
            )

    binary_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
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
        output = binary_result.stdout
        if to_version_family not in output and str(to_version) not in output:
            errors.append("Binary version does not match expected target")

    images_cmd = [
        "kubectl",
        "-n",
        namespace,
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
        elif any(image != target_image for image in images):
            errors.append(f"Unexpected pod images: {' '.join(images)}")

    if cm_version and cluster_version and cm_version != cluster_version:
        errors.append(
            f"ConfigMap {report_key} does not match cluster version ({cluster_version})"
        )

    if errors:
        print("Version check verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Version check completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
