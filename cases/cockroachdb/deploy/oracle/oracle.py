#!/usr/bin/env python3
# Verify the agent deployed the cluster to the configured spec. The image
# version (BENCH_PARAM_TO_VERSION) and per-pod storage size
# (BENCH_PARAM_STORAGE_SIZE_GI) come from the case params, so a workflow that
# overrides them is honored instead of a hardcoded value. Standalone (default
# params) this behaves identically to the old hardcoded check.
import json
import math
import os
import subprocess
import sys


TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "24.1.0")
EXPECTED_IMAGE = f"cockroachdb/cockroach:v{TO_VERSION}"
STORAGE_SIZE_GI = os.environ.get("BENCH_PARAM_STORAGE_SIZE_GI", "10")
EXPECTED_STORAGE = f"{STORAGE_SIZE_GI}Gi"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def kubectl_json(args, namespace="cockroachdb"):
    cmd = ["kubectl", "-n", namespace] + args + ["-o", "json"]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip() or "kubectl failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"


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


def _check_crdbcluster_absent(errors):
    result = run(["kubectl", "-n", "cockroachdb", "get", "crdbcluster", "-o", "json"])
    if result.returncode != 0:
        return
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return
    items = payload.get("items") or []
    if items:
        errors.append("CrdbCluster CRs detected; operator/CRDs are not allowed")


def _check_quorum_budget(labels, replicas, errors):
    pdbs, err = kubectl_json(["get", "poddisruptionbudget"])
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
        if min_value is not None and min_value >= 2:
            return
        if max_value is not None and max_value <= 1:
            return
    errors.append("No disruption budget enforces quorum safety (>=2 pods available)")


def main():
    errors = []
    svc = None
    pub_svc = None

    svc, err = kubectl_json(["get", "service", "crdb-cluster"])
    if err:
        errors.append(f"Discovery service 'crdb-cluster' not found: {err}")
    else:
        cluster_ip = svc.get("spec", {}).get("clusterIP")
        if cluster_ip not in (None, "None"):
            errors.append("Discovery service should be headless (clusterIP: None)")
        ports = _port_map(svc)
        if ports.get("grpc") != 26257:
            errors.append("Discovery service missing grpc port 26257")
        if ports.get("http") != 8080:
            errors.append("Discovery service missing http port 8080")

    pub_svc, err = kubectl_json(["get", "service", "crdb-cluster-public"])
    if err:
        errors.append(f"Public service 'crdb-cluster-public' not found: {err}")
    else:
        svc_type = pub_svc.get("spec", {}).get("type")
        if svc_type not in (None, "ClusterIP"):
            errors.append(f"Public service type should be ClusterIP, got {svc_type}")
        ports = _port_map(pub_svc)
        if ports.get("grpc") != 26257:
            errors.append("Public service missing grpc port 26257")
        if ports.get("http") != 8080:
            errors.append("Public service missing http port 8080")

    sts, err = kubectl_json(["get", "statefulset", "crdb-cluster"])
    selector_labels = {}
    pod_labels = {}
    replicas = 3
    if err:
        errors.append(f"StatefulSet 'crdb-cluster' not found: {err}")
    else:
        replicas = sts.get("spec", {}).get("replicas")
        if replicas != 3:
            errors.append(f"StatefulSet should have 3 replicas, got {replicas}")
        service_name = sts.get("spec", {}).get("serviceName")
        if service_name != "crdb-cluster":
            errors.append(f"StatefulSet serviceName should be 'crdb-cluster', got {service_name}")
        sa_name = (
            sts.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("serviceAccountName")
        )
        if sa_name != "crdb-cluster-sa":
            errors.append("StatefulSet must use the pre-provisioned ServiceAccount")
        else:
            sa, sa_err = kubectl_json(["get", "serviceaccount", sa_name])
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
            if image != EXPECTED_IMAGE:
                errors.append(f"StatefulSet image should be {EXPECTED_IMAGE}, got {image}")
            cmd_str = _container_command(container)
            if "--insecure" not in cmd_str:
                errors.append("CockroachDB must run in insecure mode for this case")
            # Accept either advertise flag: --advertise-host is the legacy spelling,
            # --advertise-addr the current one.
            if "--advertise-host" not in cmd_str and "--advertise-addr" not in cmd_str:
                errors.append("CockroachDB start command missing --advertise-host/--advertise-addr")
            if "crdb-cluster" not in cmd_str:
                errors.append("CockroachDB advertise host should use the crdb-cluster DNS")
            # The advertised address must be each pod's own stable identity: an
            # explicit POD_NAME reference, or the pod FQDN via `hostname -f`
            # (which resolves to <pod>.crdb-cluster... -- equivalent and valid).
            _pod_id = ("$(POD_NAME)", "${POD_NAME}", "$POD_NAME", "hostname -f", "$(hostname")
            if not any(tok in cmd_str for tok in _pod_id):
                errors.append("CockroachDB advertise host should use the pod's stable identity")

            required_nodes = [
                "crdb-cluster-0.crdb-cluster",
                "crdb-cluster-1.crdb-cluster",
                "crdb-cluster-2.crdb-cluster",
            ]
            missing_nodes = [node for node in required_nodes if node not in cmd_str]
            if missing_nodes:
                errors.append("CockroachDB join list must include all 3 nodes")

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
                if storage == EXPECTED_STORAGE:
                    found_storage = True
            if not found_storage:
                errors.append(f"StatefulSet volumeClaimTemplates must request {EXPECTED_STORAGE} storage")

        selector_labels = (
            sts.get("spec", {})
            .get("selector", {})
            .get("matchLabels")
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

    if selector_labels:
        label_selector = ",".join(f"{key}={value}" for key, value in selector_labels.items())
        pods, err = kubectl_json(["get", "pods", "-l", label_selector])
        if err:
            errors.append(f"Failed to list pods: {err}")
        else:
            items = pods.get("items") or []
            if len(items) < 3:
                errors.append(f"Expected 3 pods, found {len(items)}")
            ready_count = sum(1 for pod in items if _pod_ready(pod))
            if ready_count < 3:
                errors.append(f"Expected 3 ready pods, found {ready_count}")

    endpoints, err = kubectl_json(["get", "endpoints", "crdb-cluster"])
    if err:
        errors.append(f"Failed to read endpoints for crdb-cluster: {err}")
    else:
        addresses = 0
        for subset in endpoints.get("subsets") or []:
            addresses += len(subset.get("addresses") or [])
        if addresses < 3:
            errors.append(f"Expected 3 endpoints, found {addresses}")

    if pod_labels:
        _check_quorum_budget(pod_labels, replicas or 3, errors)

    _check_crdbcluster_absent(errors)

    if errors:
        print("Verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("All resources created successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
