#!/usr/bin/env python3
"""Repair RabbitMQ user permissions and inherited app-queue drift."""

import os
import sys
from pathlib import Path

COMMON = (
    Path(__file__).resolve().parents[2]
    / "vendor"
    / "import-improve-resources"
    / "resources"
    / "rabbitmq-experiments"
    / "common"
)
sys.path.insert(0, str(COMMON))

from solver_utils import kubectl_json, run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
SEED_POD = f"{CLUSTER_PREFIX}-0"


def run_rabbitmqctl(*args):
    """Run rabbitmqctl inside the first broker pod."""
    return run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            SEED_POD,
            "--",
            "rabbitmqctl",
            *args,
        ]
    )


def repair_permissions():
    """Converge the least-privilege permission grants for /app and /ops."""
    run_rabbitmqctl("set_permissions", "-p", "/app", "app-user", ".*", ".*", ".*")
    run_rabbitmqctl("set_permissions", "-p", "/ops", "ops-user", ".*", ".*", ".*")
    run_rabbitmqctl("clear_permissions", "-p", "/app", "ops-user")
    run_rabbitmqctl("clear_permissions", "-p", "/ops", "app-user")


def app_queue_has_messages():
    """Return whether /app/app-queue exists as a message-bearing queue."""
    out = run_rabbitmqctl("-q", "list_queues", "-p", "/app", "name", "messages")
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "app-queue":
            try:
                return int(parts[1]) >= 1
            except ValueError:
                return False
    return False


def app_queue_exists():
    """Return whether /app/app-queue currently exists."""
    out = run_rabbitmqctl("-q", "list_queues", "-p", "/app", "name")
    return "app-queue" in {line.strip() for line in out.splitlines() if line.strip()}


def delete_app_queue():
    """Delete /app/app-queue when an inherited declaration blocks app-client."""
    if not app_queue_exists():
        return
    run_rabbitmqctl("delete_queue", "app-queue", "-p", "/app")


def deployment_exists(name):
    """Return whether one namespaced deployment exists."""
    try:
        run(["kubectl", "-n", NAMESPACE, "get", f"deployment/{name}"])
    except RuntimeError as exc:
        if "NotFound" in str(exc):
            return False
        raise
    return True


def scale_deployment(name, replicas):
    """Scale one deployment to the requested replica count."""
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "scale",
            f"deployment/{name}",
            f"--replicas={replicas}",
        ]
    )


def pod_names(label_selector):
    """Return matching pod names ordered newest-first."""
    payload = kubectl_json("-n", NAMESPACE, "get", "pods", "-l", label_selector)
    items = payload.get("items") or []
    items.sort(
        key=lambda item: item.get("metadata", {}).get("creationTimestamp", ""),
        reverse=True,
    )
    names = []
    for item in items:
        name = (item.get("metadata") or {}).get("name") or ""
        if name:
            names.append(name)
    return names


def pod_log_text(pod_name):
    """Collect current and previous logs for one pod, best-effort."""
    chunks = []
    for previous in (False, True):
        cmd = ["kubectl", "-n", NAMESPACE, "logs", pod_name, "--tail", "200"]
        if previous:
            cmd.append("--previous")
        try:
            output = run(cmd).strip()
        except RuntimeError:
            continue
        if output:
            chunks.append(output)
    return "\n".join(chunks)


def app_client_log_text():
    """Collect the newest app-client pod logs for failure diagnosis."""
    chunks = []
    for pod_name in pod_names("app=app-client")[:3]:
        output = pod_log_text(pod_name)
        if output:
            chunks.append(f"== {pod_name} ==\n{output}")
    return "\n\n".join(chunks)


def has_queue_declaration_conflict(log_text):
    """Detect an immutable queue declaration mismatch from app-client logs."""
    lowered = log_text.lower()
    return "app-queue" in lowered and (
        "inequivalent arg" in lowered or "precondition_failed" in lowered
    )


def restart_deployment(name):
    """Restart one deployment so the fixed state is retried immediately."""
    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"deployment/{name}"])


def quiesce_inherited_app_producer():
    """Stop classic_queue carryover that can recreate the incompatible app-queue."""
    if not deployment_exists("app-producer"):
        return
    scale_deployment("app-producer", 0)
    wait_until(
        lambda: not pod_names("app=app-producer"),
        timeout_sec=180,
        interval_sec=5,
        description="app-producer pods to terminate",
    )
    delete_app_queue()


def reconcile_app_queue_if_needed():
    """Delete only the inherited app-queue drift that blocks app-client startup."""
    logs = app_client_log_text()
    if not has_queue_declaration_conflict(logs):
        raise RuntimeError(
            "app-client did not become ready after permission repair and no queue "
            f"declaration conflict was detected in logs:\n{logs}"
        )
    delete_app_queue()
    restart_deployment("app-client")


def main():
    """Repair permissions, reconcile inherited queue drift, and wait for readiness."""
    repair_permissions()
    quiesce_inherited_app_producer()

    restart_deployment("app-client")
    restart_deployment("ops-client")

    wait_deployment_ready(NAMESPACE, "ops-client", timeout_sec=300)
    try:
        wait_deployment_ready(NAMESPACE, "app-client", timeout_sec=120)
    except RuntimeError:
        reconcile_app_queue_if_needed()
        wait_deployment_ready(NAMESPACE, "app-client", timeout_sec=300)

    wait_until(
        app_queue_has_messages,
        timeout_sec=180,
        interval_sec=5,
        description="app-queue to contain messages",
    )
    print("manual_user_permission solver applied")


if __name__ == "__main__":
    main()
