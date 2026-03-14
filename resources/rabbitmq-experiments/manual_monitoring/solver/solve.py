#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import apply_yaml, kubectl_json, run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")


PROM_CONFIG = f"""\
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: {NAMESPACE}
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: "prometheus"
        static_configs:
          - targets: ["localhost:9090"]
      - job_name: "rabbitmq"
        metrics_path: /metrics
        static_configs:
          - targets:
              - rabbitmq-0.rabbitmq-headless.{NAMESPACE}.svc.cluster.local:15692
              - rabbitmq-1.rabbitmq-headless.{NAMESPACE}.svc.cluster.local:15692
              - rabbitmq-2.rabbitmq-headless.{NAMESPACE}.svc.cluster.local:15692
"""


def get_prometheus_pod():
    pods = kubectl_json("-n", NAMESPACE, "get", "pods", "-l", "app=prometheus")
    items = pods.get("items") or []
    if not items:
        raise RuntimeError("prometheus pod not found")
    return items[0]["metadata"]["name"]


def rabbitmq_targets_up():
    pod = get_prometheus_pod()
    url = "http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22rabbitmq%22%7D"
    out = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "wget", "-qO-", url])
    payload = json.loads(out)
    result = ((payload.get("data") or {}).get("result")) or []
    if len(result) < 3:
        return False
    for sample in result:
        value = (sample.get("value") or [None, "0"])[1]
        if value not in ("1", "1.0"):
            return False
    return True


def main():
    apply_yaml(PROM_CONFIG)
    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment/prometheus"])
    wait_deployment_ready(NAMESPACE, "prometheus", timeout_sec=300)
    wait_until(
        rabbitmq_targets_up,
        timeout_sec=180,
        interval_sec=5,
        description="prometheus rabbitmq targets to be up",
    )
    print("manual_monitoring solver applied")


if __name__ == "__main__":
    main()
