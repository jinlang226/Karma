#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import textwrap
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

from setup_check_utils import (  # noqa: E402
    expect_pod_ready,
    expect_pods_ready,
    list_pods,
    pod_is_ready,
    run_json,
    run,
    split_lines,
)

BLUE_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_BLUE_CLUSTER_PREFIX", "rabbitmq-blue")
GREEN_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_GREEN_CLUSTER_PREFIX", "rabbitmq-green")
SOURCE_NAMESPACE = os.environ.get("BENCH_NS_SOURCE")
TARGET_NAMESPACE = os.environ.get("BENCH_NS_TARGET")


def _check_cluster_bootstrap(ns, cluster_prefix, label, errors):
    pod0 = f"{cluster_prefix}-0"
    try:
        queues = run(
            [
                "kubectl",
                "-n",
                ns,
                "exec",
                pod0,
                "--",
                "rabbitmqctl",
                "-q",
                "list_queues",
                "-p",
                "/app",
                "name",
            ]
        )
        if "app-queue" not in set(split_lines(queues)):
            errors.append(f"{label}: app-queue missing from /app")
    except Exception as exc:
        errors.append(f"{label}: failed to inspect bootstrap queue state: {exc}")


def _check_seed_state(source_ns, source_prefix, required_messages, errors):
    _check_seed_state_with_mode(
        source_ns,
        source_prefix,
        required_messages,
        errors,
        exact=False,
    )


def _read_secret_value(namespace, secret_name, key, label, errors):
    try:
        secret = run_json(["kubectl", "-n", namespace, "get", "secret", secret_name, "-o", "json"])
    except Exception as exc:
        errors.append(f"{label}: failed to read secret {secret_name}: {exc}")
        return None

    raw = ((secret.get("data") or {}).get(key) or "").strip()
    if not raw:
        errors.append(f"{label}: secret {secret_name} missing key {key}")
        return None
    try:
        return base64.b64decode(raw).decode().strip()
    except Exception as exc:
        errors.append(f"{label}: secret {secret_name}/{key} decode failed: {exc}")
        return None


def _parse_version_major(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    match = re.search(r"\b(\d+)\.\d+(?:\.\d+)?\b", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _detect_rabbitmq_major(namespace, cluster_prefix):
    try:
        out = run(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                f"{cluster_prefix}-0",
                "--",
                "rabbitmqctl",
                "version",
            ]
        ).strip()
    except Exception:
        return None
    return _parse_version_major(out)


def _load_json_last_line(raw):
    lines = split_lines(raw)
    candidate = lines[-1] if lines else str(raw or "").strip()
    if not candidate:
        return None
    return json.loads(candidate)


def _fetch_seed_batch_via_python(source_ns, source_prefix, auth_pair, fetch_count):
    script = textwrap.dedent(
        f"""
        import base64
        import json
        import urllib.request

        auth = base64.b64encode({auth_pair!r}.encode("utf-8")).decode("ascii")
        headers = {{
            "Authorization": "Basic " + auth,
            "Content-Type": "application/json",
        }}
        payload = json.dumps({{
            "count": {int(fetch_count)},
            "ackmode": "ack_requeue_true",
            "encoding": "auto",
            "truncate": 50000,
        }}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:15672/api/queues/%2Fapp/app-queue/get",
            data=payload,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            batch = json.load(resp)
        print(json.dumps(batch))
        """
    ).strip()
    out = run(
        [
            "kubectl",
            "-n",
            source_ns,
            "exec",
            f"{source_prefix}-0",
            "--",
            "python3",
            "-c",
            script,
        ]
    )
    parsed = _load_json_last_line(out)
    if not isinstance(parsed, list):
        raise ValueError("python probe did not return a JSON list")
    return parsed


def _fetch_seed_batch_via_curl(source_ns, source_prefix, user, password, fetch_count):
    payload = json.dumps(
        {
            "count": int(fetch_count),
            "ackmode": "ack_requeue_true",
            "encoding": "auto",
            "truncate": 50000,
        }
    )
    out = run(
        [
            "kubectl",
            "-n",
            source_ns,
            "exec",
            f"{source_prefix}-0",
            "--",
            "curl",
            "-sSf",
            "-u",
            f"{user}:{password}",
            "-H",
            "content-type: application/json",
            "-X",
            "POST",
            "-d",
            payload,
            "http://localhost:15672/api/queues/%2Fapp/app-queue/get",
        ]
    )
    parsed = _load_json_last_line(out)
    if not isinstance(parsed, list):
        raise ValueError("curl probe did not return a JSON list")
    return parsed


def _check_seed_id_coverage(source_ns, source_prefix, seed_count, errors):
    if seed_count <= 0:
        return

    admin_secret = f"{source_prefix}-admin"
    user = _read_secret_value(source_ns, admin_secret, "username", "source", errors)
    password = _read_secret_value(source_ns, admin_secret, "password", "source", errors)
    if not user or not password:
        return

    fetch_count = max(seed_count * 20, 1000)
    auth_pair = f"{user}:{password}"
    major = _detect_rabbitmq_major(source_ns, source_prefix)
    # 4.x images may not ship python3 in-container; prefer curl there.
    methods = ("curl", "python") if major and major >= 4 else ("python", "curl")

    batch = None
    probe_errors = []
    for method in methods:
        try:
            if method == "python":
                batch = _fetch_seed_batch_via_python(source_ns, source_prefix, auth_pair, fetch_count)
            else:
                batch = _fetch_seed_batch_via_curl(source_ns, source_prefix, user, password, fetch_count)
            break
        except Exception as exc:
            probe_errors.append(f"{method}={exc}")

    if batch is None:
        # RabbitMQ 4.x management images can lack both python3 and curl in-container.
        # In that case we fall back to strict queue count checks in seed-data mode.
        if major and major >= 4:
            return
        details = "; ".join(probe_errors) if probe_errors else "no probe methods attempted"
        errors.append(f"source: failed to inspect seed id coverage: {details}")
        return

    found_ids = set()
    for msg in batch:
        if not isinstance(msg, dict):
            continue
        try:
            parsed = json.loads(msg.get("payload", ""))
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        raw_id = parsed.get("id")
        try:
            found_ids.add(int(raw_id))
        except Exception:
            continue

    if len(found_ids) < seed_count:
        missing = [str(i) for i in range(1, seed_count + 1) if i not in found_ids]
        preview = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
        batch_len = len(batch)
        errors.append(
            f"source: seed range 1..N not fully present on /app/app-queue "
            f"(missing: {preview}; inspected_batch={batch_len})"
        )


def _check_seed_state_with_mode(source_ns, source_prefix, required_messages, errors, *, exact):
    try:
        queue_out = run(
            [
                "kubectl",
                "-n",
                source_ns,
                "exec",
                f"{source_prefix}-0",
                "--",
                "rabbitmqctl",
                "-q",
                "list_queues",
                "-p",
                "/app",
                "name",
                "messages",
            ]
        )
        seen = False
        for line in split_lines(queue_out):
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "app-queue":
                seen = True
                try:
                    count = int(parts[1])
                    if exact:
                        if count == required_messages:
                            return
                        errors.append(
                            f"source: app-queue expected exactly {required_messages} message(s), found {count}"
                        )
                        return
                    if count >= required_messages:
                        return
                except ValueError:
                    pass
                break
        if seen:
            errors.append(f"source: app-queue has fewer than {required_messages} message(s)")
        else:
            errors.append("source: app-queue missing from /app")
    except Exception as exc:
        errors.append(f"source: failed to inspect seed queue state: {exc}")


def _check_target_queue_empty(target_ns, target_prefix, errors, *, max_messages=0):
    try:
        queue_out = run(
            [
                "kubectl",
                "-n",
                target_ns,
                "exec",
                f"{target_prefix}-0",
                "--",
                "rabbitmqctl",
                "-q",
                "list_queues",
                "-p",
                "/app",
                "name",
                "messages",
            ]
        )
        for line in split_lines(queue_out):
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "app-queue":
                try:
                    count = int(parts[1])
                except ValueError:
                    errors.append("target: app-queue message count is not an integer")
                    return
                if count > max_messages:
                    errors.append(
                        f"target: app-queue expected <= {max_messages} messages, found {count}"
                    )
                return
        errors.append("target: app-queue missing from /app")
    except Exception as exc:
        errors.append(f"target: failed to inspect app-queue on target: {exc}")


def _check_target_app_client_quiesced(target_ns, errors):
    try:
        deploy = run_json(["kubectl", "-n", target_ns, "get", "deployment", "app-client", "-o", "json"])
    except Exception:
        # app-client may not exist in some baseline states; that is still quiesced.
        return

    desired = int(((deploy.get("spec") or {}).get("replicas") or 0))
    ready = int(((deploy.get("status") or {}).get("readyReplicas") or 0))
    if desired != 0:
        errors.append(f"target: app-client deployment replicas should be 0, found {desired}")
    if ready != 0:
        errors.append(f"target: app-client deployment readyReplicas should be 0, found {ready}")

    pods = list_pods(target_ns, label="app=app-client")
    live_pods = []
    for pod in pods:
        status = pod.get("status") or {}
        phase = status.get("phase", "Unknown")
        if phase in {"Succeeded", "Failed"}:
            continue
        name = ((pod.get("metadata") or {}).get("name") or "<unknown>")
        live_pods.append(f"{name}({phase})")
    if live_pods:
        errors.append("target: app-client pods still active: " + ", ".join(sorted(live_pods)))


def _read_seed_count(namespace, label, errors):
    try:
        cm = run_json(["kubectl", "-n", namespace, "get", "configmap", "migration-seed", "-o", "json"])
    except Exception as exc:
        errors.append(f"{label}: migration-seed configmap missing: {exc}")
        return None

    raw = str(((cm.get("data") or {}).get("seed_count") or "")).strip()
    if not raw:
        errors.append(f"{label}: migration-seed.seed_count missing")
        return None
    try:
        seed_count = int(raw)
    except ValueError:
        errors.append(f"{label}: migration-seed.seed_count is not an integer ({raw!r})")
        return None
    if seed_count <= 0:
        errors.append(f"{label}: migration-seed.seed_count must be > 0 (got {seed_count})")
        return None
    return seed_count


def _expect_labeled_pod_ready(namespace, label, errors, name_hint):
    pods = list_pods(namespace, label=label)
    if not pods:
        errors.append(f"{name_hint}: no pods found")
        return
    if not any(pod_is_ready(p) for p in pods):
        errors.append(f"{name_hint}: no ready pod found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "rabbitmq"))
    parser.add_argument("--source-namespace", default=SOURCE_NAMESPACE)
    parser.add_argument("--target-namespace", default=TARGET_NAMESPACE)
    parser.add_argument("--min-ready", type=int, default=1)
    parser.add_argument("--bootstrap-only", action="store_true")
    parser.add_argument("--bootstrap-source-only", action="store_true")
    parser.add_argument("--bootstrap-target-only", action="store_true")
    parser.add_argument("--seed-config-source-only", action="store_true")
    parser.add_argument("--seed-config-target-only", action="store_true")
    parser.add_argument("--seed-data-source-only", action="store_true")
    parser.add_argument("--target-queue-empty-only", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()

    source_ns = args.source_namespace or args.namespace
    target_ns = args.target_namespace or args.namespace
    errors = []

    if args.bootstrap_source_only:
        expect_pods_ready(source_ns, f"app={BLUE_CLUSTER_PREFIX}", 3, errors, BLUE_CLUSTER_PREFIX)
        _check_cluster_bootstrap(source_ns, BLUE_CLUSTER_PREFIX, "source", errors)
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.bootstrap_target_only:
        expect_pods_ready(target_ns, f"app={GREEN_CLUSTER_PREFIX}", 3, errors, GREEN_CLUSTER_PREFIX)
        _check_cluster_bootstrap(target_ns, GREEN_CLUSTER_PREFIX, "target", errors)
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.seed_config_source_only:
        _read_seed_count(source_ns, "source", errors)
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.seed_config_target_only:
        _read_seed_count(target_ns, "target", errors)
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.seed_data_source_only:
        expect_pods_ready(source_ns, f"app={BLUE_CLUSTER_PREFIX}", 3, errors, BLUE_CLUSTER_PREFIX)
        _check_cluster_bootstrap(source_ns, BLUE_CLUSTER_PREFIX, "source", errors)
        source_seed_count = _read_seed_count(source_ns, "source", errors)
        required_seed_messages = source_seed_count if source_seed_count is not None else 1
        _check_seed_state_with_mode(
            source_ns,
            BLUE_CLUSTER_PREFIX,
            required_seed_messages,
            errors,
            exact=False,
        )
        if source_seed_count is not None:
            _check_seed_id_coverage(source_ns, BLUE_CLUSTER_PREFIX, source_seed_count, errors)
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.target_queue_empty_only:
        expect_pods_ready(target_ns, f"app={GREEN_CLUSTER_PREFIX}", 3, errors, GREEN_CLUSTER_PREFIX)
        _check_cluster_bootstrap(target_ns, GREEN_CLUSTER_PREFIX, "target", errors)
        _check_target_app_client_quiesced(target_ns, errors)
        _check_target_queue_empty(
            target_ns,
            GREEN_CLUSTER_PREFIX,
            errors,
            max_messages=0,
        )
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    expect_pods_ready(source_ns, f"app={BLUE_CLUSTER_PREFIX}", 3, errors, BLUE_CLUSTER_PREFIX)
    expect_pods_ready(target_ns, f"app={GREEN_CLUSTER_PREFIX}", 3, errors, GREEN_CLUSTER_PREFIX)
    _check_cluster_bootstrap(source_ns, BLUE_CLUSTER_PREFIX, "source", errors)
    _check_cluster_bootstrap(target_ns, GREEN_CLUSTER_PREFIX, "target", errors)

    if args.bootstrap_only:
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    source_seed_count = _read_seed_count(source_ns, "source", errors)
    target_seed_count = _read_seed_count(target_ns, "target", errors)
    if (
        source_seed_count is not None
        and target_seed_count is not None
        and source_seed_count != target_seed_count
    ):
        errors.append(
            f"seed_count mismatch between source ({source_seed_count}) and target ({target_seed_count})"
        )

    required_seed_messages = source_seed_count if source_seed_count is not None else 1
    _check_seed_state(source_ns, BLUE_CLUSTER_PREFIX, required_seed_messages, errors)
    _check_target_queue_empty(target_ns, GREEN_CLUSTER_PREFIX, errors)

    if args.seed_only:
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    _expect_labeled_pod_ready(target_ns, "app=curl-test", errors, "curl-test")

    deploys = run_json(
        ["kubectl", "-n", source_ns, "get", "deploy", "-l", "app=blue-producer", "-o", "json"]
    ).get("items", [])
    if not deploys:
        errors.append("blue-producer deployment missing")
    else:
        if not any((((d.get("status") or {}).get("readyReplicas") or 0) >= 1) for d in deploys):
            errors.append("blue-producer is not Ready")

    if errors:
        print("setup-precondition-check: failed")
        for err in errors:
            print(f" - {err}")
        return 1
    print("setup-precondition-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
