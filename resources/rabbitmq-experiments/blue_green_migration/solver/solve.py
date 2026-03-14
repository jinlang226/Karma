#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))
BLUE_PRODUCER_TEMPLATE = Path(__file__).resolve().parents[1] / "resource" / "blue-producer.yaml"

from solver_utils import kubectl_json, run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
SOURCE_NAMESPACE = os.environ.get("BENCH_NS_SOURCE", NAMESPACE)
TARGET_NAMESPACE = os.environ.get("BENCH_NS_TARGET", NAMESPACE)
BLUE_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_BLUE_CLUSTER_PREFIX", "rabbitmq-blue")
GREEN_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_GREEN_CLUSTER_PREFIX", "rabbitmq-green")


GREEN_SEED_JOB = """apiVersion: batch/v1
kind: Job
metadata:
  name: __GREEN_CLUSTER_PREFIX__-seed-solver
  namespace: __BENCH_NAMESPACE__
spec:
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: seed
          image: python:3.11-alpine
          env:
            - name: ADMIN_USER
              valueFrom:
                secretKeyRef:
                  name: __GREEN_CLUSTER_PREFIX__-admin
                  key: username
            - name: ADMIN_PASS
              valueFrom:
                secretKeyRef:
                  name: __GREEN_CLUSTER_PREFIX__-admin
                  key: password
            - name: SEED_COUNT
              valueFrom:
                configMapKeyRef:
                  name: migration-seed
                  key: seed_count
          command:
            - /bin/sh
            - -c
            - |
              set -e
              python - <<'PY'
              import json, os, sys, urllib.request

              user = os.environ['ADMIN_USER']
              pw = os.environ['ADMIN_PASS']
              seed_count = int(os.environ.get('SEED_COUNT', '0'))
              if seed_count <= 0:
                  raise SystemExit('seed_count missing/invalid')

              auth = (f"{user}:{pw}").encode('utf-8')
              import base64
              headers = {
                  'authorization': 'Basic ' + base64.b64encode(auth).decode('ascii'),
                  'content-type': 'application/json',
              }

              base = 'http://__GREEN_CLUSTER_PREFIX__:15672/api'

              def req(method, path, payload=None):
                  body = None if payload is None else json.dumps(payload).encode('utf-8')
                  request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
                  with urllib.request.urlopen(request, timeout=10) as resp:
                      return resp.read().decode('utf-8')

              req('PUT', '/vhosts/%2Fapp', {})
              req('PUT', '/queues/%2Fapp/app-queue', {'durable': True, 'auto_delete': False, 'arguments': {'x-queue-type': 'classic'}})

              for i in range(1, seed_count + 1):
                  payload = {
                      'properties': {'delivery_mode': 2},
                      'routing_key': 'app-queue',
                      'payload': json.dumps({'id': i}),
                      'payload_encoding': 'string',
                  }
                  out = req('POST', '/exchanges/%2Fapp/amq.default/publish', payload)
                  data = json.loads(out)
                  if not data.get('routed'):
                      raise SystemExit(f'publish failed for id={i}: {out}')
              PY
""".replace("__BENCH_NAMESPACE__", TARGET_NAMESPACE).replace("__GREEN_CLUSTER_PREFIX__", GREEN_CLUSTER_PREFIX)


def producer_deployment_name(namespace):
    configured = str(os.environ.get("BENCH_PARAM_PRODUCER_DEPLOYMENT_NAME") or "").strip()
    if configured:
        return configured
    suffix = __import__("hashlib").sha1(namespace.encode("utf-8")).hexdigest()[:8]
    return f"blue-producer-{suffix}"


def get_blue_producer_deployment(namespace):
    deploys = kubectl_json("-n", namespace, "get", "deployment", "-l", "app=blue-producer").get("items") or []
    if not deploys:
        return None
    return deploys[0]


def render_producer_command(base_url):
    return (
        "set -e\n"
        f'base="{base_url}"\n'
        'auth="${RABBITMQ_APP_USER}:${RABBITMQ_APP_PASS}"\n'
        "i=$((SEED_COUNT + 1))\n"
        "while true; do\n"
        "  body=$(printf '{\"properties\":{\"delivery_mode\":2},\"routing_key\":\"app-queue\",\"payload\":\"{\\\\\"id\\\\\":%s}\",\"payload_encoding\":\"string\"}' \"$i\")\n"
        "  curl -s -u \"$auth\" -H \"content-type: application/json\" \\\n"
        "    -XPOST \"$base/exchanges/%2Fapp/amq.default/publish\" \\\n"
        "    -d \"$body\" \\\n"
        "    | grep -q '\"routed\":true' \\\n"
        "    && echo \"blue-producer: published $i\" \\\n"
        "    || echo \"blue-producer: publish failed\" >&2\n"
        "  i=$((i+1))\n"
        "  sleep 1\n"
        "done"
    )


def green_queue_seeded(seed_count):
    out = run(
        [
            "kubectl",
            "-n",
            TARGET_NAMESPACE,
            "exec",
            f"{GREEN_CLUSTER_PREFIX}-0",
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
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "app-queue":
            try:
                return int(parts[1]) >= seed_count
            except ValueError:
                return False
    return False


def green_queue_has_seed_and_live(seed_count):
    fetch_count = max(seed_count + 20, seed_count * 3)
    try:
        raw = run(
            [
                "kubectl",
                "-n",
                TARGET_NAMESPACE,
                "exec",
                "curl-test",
                "--",
                "curl",
                "-sS",
                "-u",
                "admin:adminpass",
                "-H",
                "content-type: application/json",
                "-X",
                "POST",
                "-d",
                json.dumps(
                    {
                        "count": fetch_count,
                        "ackmode": "ack_requeue_true",
                        "encoding": "auto",
                        "truncate": 50000,
                    }
                ),
                f"http://{GREEN_CLUSTER_PREFIX}:15672/api/queues/%2Fapp/app-queue/get",
            ],
        )
    except Exception:
        return False

    try:
        batch = json.loads(raw)
    except Exception:
        return False

    found_ids = set()
    has_live = False
    for msg in batch:
        raw_payload = msg.get("payload", "")
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        try:
            msg_id = int(parsed.get("id"))
        except Exception:
            continue
        if 1 <= msg_id <= seed_count:
            found_ids.add(msg_id)
        elif msg_id > seed_count:
            has_live = True

    if len(found_ids) != seed_count:
        return False
    if not has_live:
        return False
    return True


def main():
    seed_cfg = kubectl_json("-n", TARGET_NAMESPACE, "get", "configmap", "migration-seed")
    seed_count = int(((seed_cfg.get("data") or {}).get("seed_count") or "0"))
    if seed_count <= 0:
        raise RuntimeError("migration-seed seed_count missing/invalid")

    seed_job = f"{GREEN_CLUSTER_PREFIX}-seed-solver"
    run(["kubectl", "-n", TARGET_NAMESPACE, "delete", "job", seed_job, "--ignore-not-found=true"])
    run(["kubectl", "-n", TARGET_NAMESPACE, "apply", "-f", "-"], input_text=GREEN_SEED_JOB)
    run(
        [
            "kubectl",
            "-n",
            TARGET_NAMESPACE,
            "wait",
            "--for=condition=complete",
            f"job/{seed_job}",
            "--timeout=300s",
        ]
    )

    deploy = get_blue_producer_deployment(SOURCE_NAMESPACE)
    producer_name = producer_deployment_name(SOURCE_NAMESPACE)
    if deploy is None:
        if not BLUE_PRODUCER_TEMPLATE.exists():
            raise RuntimeError(f"missing blue-producer template: {BLUE_PRODUCER_TEMPLATE}")
        producer_manifest = BLUE_PRODUCER_TEMPLATE.read_text(encoding="utf-8").replace(
            "${BENCH_PARAM_BLUE_CLUSTER_PREFIX}",
            BLUE_CLUSTER_PREFIX,
        ).replace(
            "__BLUE_PRODUCER_DEPLOYMENT_NAME__",
            producer_name,
        )
        run(
            [
                "kubectl",
                "-n",
                SOURCE_NAMESPACE,
                "apply",
                "-f",
                "-",
            ]
            ,
            input_text=producer_manifest,
        )
        wait_deployment_ready(SOURCE_NAMESPACE, producer_name, timeout_sec=300)
        deploy = kubectl_json("-n", SOURCE_NAMESPACE, "get", "deployment", producer_name)
    else:
        producer_name = ((deploy.get("metadata") or {}).get("name") or producer_name)
    container = deploy["spec"]["template"]["spec"]["containers"][0]
    container["command"][2] = render_producer_command(
        f"http://{GREEN_CLUSTER_PREFIX}.{TARGET_NAMESPACE}:15672/api"
    )
    deploy.pop("status", None)
    run(["kubectl", "-n", SOURCE_NAMESPACE, "apply", "-f", "-"], input_text=json.dumps(deploy))

    wait_deployment_ready(SOURCE_NAMESPACE, producer_name, timeout_sec=300)
    wait_until(
        lambda: green_queue_seeded(seed_count) and green_queue_has_seed_and_live(seed_count),
        timeout_sec=180,
        interval_sec=5,
        description="green app-queue to contain seed range plus live post-seed traffic",
    )
    print("blue_green_migration solver applied")


if __name__ == "__main__":
    main()
