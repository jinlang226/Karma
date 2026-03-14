#!/usr/bin/env python3
import math
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    bench_param_int,
    cluster_prefix,
    cluster_public_service,
    cluster_service,
    cluster_service_account,
    cockroach_image,
    kubectl_json,
)


def _port_map(service):
    ports = service.get("spec", {}).get("ports", [])
    return {port.get("name"): port.get("port") for port in ports if port.get("name")}


def _selector_matches_labels(selector, labels):
    if not selector or not labels:
        return False
    match_labels = selector.get("matchLabels") or {}
    for key, value in match_labels.items():
        if labels.get(key) != value:
            return False
    match_exprs = selector.get("matchExpressions") or []
    for expr in match_exprs:
        key = expr.get("key")
        op = expr.get("operator")
        values = expr.get("values") or []
        if op == "In":
            if labels.get(key) not in values:
                return False
        elif op == "NotIn":
            if labels.get(key) in values:
                return False
        elif op == "Exists":
            if key not in labels:
                return False
        elif op == "DoesNotExist":
            if key in labels:
                return False
        else:
            return False
    return True


def _parse_budget_value(value, total):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            try:
                pct = int(text[:-1])
            except ValueError:
                return None
            return int(math.ceil(total * pct / 100.0))
        if text.isdigit():
            return int(text)
    return None


def _pod_ready(pod):
    conditions = pod.get("status", {}).get("conditions") or []
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def _container_command(container):
    parts = []
    for field in ("command", "args"):
        for item in container.get(field) or []:
            parts.append(str(item))
    return " ".join(parts)


def _select_container(containers):
    if not containers:
        return None
    for container in containers:
        image = str(container.get("image") or "")
        if image.startswith("cockroachdb/cockroach"):
            return container
    return containers[0]


def _check_crdbcluster_absent(namespace, errors):
    payload, err = kubectl_json(namespace, ["get", "crdbcluster"])
    if err:
        return
    items = payload.get("items") or []
    if items:
        errors.append("CrdbCluster CRs detected; operator/CRDs are not allowed")


def _check_quorum_budget(namespace, labels, replicas, errors):
    pdbs, err = kubectl_json(namespace, ["get", "poddisruptionbudget"])
    if err:
        errors.append(f"Failed to read PodDisruptionBudgets: {err}")
        return
    items = pdbs.get("items") or []
    for pdb in items:
        selector = (pdb.get("spec") or {}).get("selector") or {}
        if not _selector_matches_labels(selector, labels):
            continue
        spec = pdb.get("spec") or {}
        min_available = spec.get("minAvailable")
        max_unavailable = spec.get("maxUnavailable")
        min_value = _parse_budget_value(min_available, replicas) if min_available is not None else None
        max_value = _parse_budget_value(max_unavailable, replicas) if max_unavailable is not None else None
        if min_value is not None and min_value >= max(1, replicas - 1):
            return
        if max_value is not None and max_value <= 1:
            return
    errors.append("No disruption budget enforces quorum safety")


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    replicas_expected = bench_param_int("replica_count", 3)
    to_version = bench_param("to_version", "24.1.0")
    storage_size_gi = bench_param_int("storage_size_gi", 10)

    discovery_service_name = cluster_service(prefix)
    public_service_name = cluster_public_service(prefix)
    statefulset_name = prefix
    service_account_name = cluster_service_account(prefix)
    expected_image = cockroach_image(to_version)
    expected_storage = f"{storage_size_gi}Gi"

    errors = []
    svc = None
    pub_svc = None

    svc, err = kubectl_json(namespace, ["get", "service", discovery_service_name])
    if err:
        errors.append(f"Discovery service '{discovery_service_name}' not found: {err}")
    else:
        cluster_ip = svc.get("spec", {}).get("clusterIP")
        if cluster_ip not in (None, "None"):
            errors.append("Discovery service should be headless (clusterIP: None)")
        ports = _port_map(svc)
        if ports.get("grpc") != 26257:
            errors.append("Discovery service missing grpc port 26257")
        if ports.get("http") != 8080:
            errors.append("Discovery service missing http port 8080")

    pub_svc, err = kubectl_json(namespace, ["get", "service", public_service_name])
    if err:
        errors.append(f"Public service '{public_service_name}' not found: {err}")
    else:
        svc_type = pub_svc.get("spec", {}).get("type")
        if svc_type not in (None, "ClusterIP"):
            errors.append(f"Public service type should be ClusterIP, got {svc_type}")
        ports = _port_map(pub_svc)
        if ports.get("grpc") != 26257:
            errors.append("Public service missing grpc port 26257")
        if ports.get("http") != 8080:
            errors.append("Public service missing http port 8080")

    sts, err = kubectl_json(namespace, ["get", "statefulset", statefulset_name])
    selector_labels = {}
    pod_labels = {}
    replicas = replicas_expected
    if err:
        errors.append(f"StatefulSet '{statefulset_name}' not found: {err}")
    else:
        replicas = sts.get("spec", {}).get("replicas")
        if replicas != replicas_expected:
            errors.append(f"StatefulSet should have {replicas_expected} replicas, got {replicas}")
        service_name = sts.get("spec", {}).get("serviceName")
        if service_name != discovery_service_name:
            errors.append(
                f"StatefulSet serviceName should be '{discovery_service_name}', got {service_name}"
            )

        sa_name = (
            sts.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("serviceAccountName")
        )
        if sa_name != service_account_name:
            errors.append(
                f"StatefulSet must use ServiceAccount '{service_account_name}', got '{sa_name}'"
            )
        else:
            _, sa_err = kubectl_json(namespace, ["get", "serviceaccount", sa_name])
            if sa_err:
                errors.append(f"ServiceAccount '{sa_name}' not found: {sa_err}")

        containers = (
            sts.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers")
        ) or []
        container = _select_container(containers)
        if not container:
            errors.append("StatefulSet has no containers")
        else:
            image = container.get("image")
            if image != expected_image:
                errors.append(f"StatefulSet image should be {expected_image}, got {image}")
            cmd_str = _container_command(container)
            if "--insecure" not in cmd_str:
                errors.append("CockroachDB must run in insecure mode for this case")
            if "--advertise-host" not in cmd_str:
                errors.append("CockroachDB start command missing --advertise-host")
            if discovery_service_name not in cmd_str:
                errors.append("CockroachDB advertise host should use the cluster DNS")
            if "$(POD_NAME)" not in cmd_str and "${POD_NAME}" not in cmd_str and "$POD_NAME" not in cmd_str:
                errors.append("CockroachDB advertise host should use the pod name")

            required_nodes = [
                f"{prefix}-{index}.{discovery_service_name}" for index in range(replicas_expected)
            ]
            missing_nodes = [node for node in required_nodes if node not in cmd_str]
            if missing_nodes:
                errors.append("CockroachDB join list must include all expected nodes")

        vclaim_templates = sts.get("spec", {}).get("volumeClaimTemplates", []) or []
        if not vclaim_templates:
            errors.append("StatefulSet missing volumeClaimTemplates")
        else:
            found_storage = False
            for tmpl in vclaim_templates:
                storage = (
                    tmpl.get("spec", {})
                    .get("resources", {})
                    .get("requests", {})
                    .get("storage")
                )
                if storage == expected_storage:
                    found_storage = True
            if not found_storage:
                errors.append(
                    f"StatefulSet volumeClaimTemplates must request {expected_storage} storage"
                )

        selector_labels = (
            sts.get("spec", {}).get("selector", {}).get("matchLabels")
        ) or {}
        pod_labels = (
            sts.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels")
        ) or {}
        if not selector_labels:
            errors.append("StatefulSet selector labels are missing")

    if svc and pod_labels:
        selector = svc.get("spec", {}).get("selector") or {}
        for key, value in selector.items():
            if pod_labels.get(key) != value:
                errors.append("Discovery service selector does not match pod labels")
                break
    if pub_svc and pod_labels:
        selector = pub_svc.get("spec", {}).get("selector") or {}
        for key, value in selector.items():
            if pod_labels.get(key) != value:
                errors.append("Public service selector does not match pod labels")
                break

    pods, err = kubectl_json(namespace, ["get", "pods", "-l", "app.kubernetes.io/name=cockroachdb"])
    if err:
        errors.append(f"Failed to read CockroachDB pods: {err}")
    else:
        items = pods.get("items") or []
        if len(items) != replicas_expected:
            errors.append(f"Expected {replicas_expected} pods, found {len(items)}")
        for pod in items:
            name = pod.get("metadata", {}).get("name", "unknown")
            phase = pod.get("status", {}).get("phase")
            if phase != "Running":
                errors.append(f"Pod {name} is not Running (phase: {phase})")
            if not _pod_ready(pod):
                errors.append(f"Pod {name} is not Ready")

    if selector_labels:
        _check_quorum_budget(namespace, selector_labels, replicas_expected, errors)

    endpoints, err = kubectl_json(namespace, ["get", "endpoints", discovery_service_name])
    if err:
        errors.append(f"Failed to read endpoints for {discovery_service_name}: {err}")
    else:
        subsets = endpoints.get("subsets") or []
        address_count = 0
        for subset in subsets:
            address_count += len(subset.get("addresses") or [])
        if address_count == 0:
            errors.append("Discovery service has no ready endpoint addresses")

    _check_crdbcluster_absent(namespace, errors)

    if errors:
        print("Deployment verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Deployment verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
