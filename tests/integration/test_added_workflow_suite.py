"""
Structural regression coverage for the workflow suite added in this change.

These tests deliberately do not run an agent or touch a cluster. They verify
that each workflow can be loaded by the same definitions layer used by the CLI,
resolved against real cases/adversary scenarios, and surfaced as OK by the HTTP
catalog used by the UI.
"""

from pathlib import Path

import pytest

from karma.definitions.workflows import (
    load_workflow_file,
    normalize_workflow,
    resolve_workflow_rows,
)
from karma.interfaces.http.catalog import list_workflow_files


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESOURCES_DIR = _REPO_ROOT / "cases"
_WORKFLOWS_DIR = _REPO_ROOT / "workflows"


ADDED_WORKFLOW_PATHS = (
    "long/rabbitmq-security-marathon.yaml",
    "short/cockroachdb-block-sql-adversary.yaml",
    "short/cockroachdb-cert-ingress-monitor.yaml",
    "short/cockroachdb-delete-cert-adversary.yaml",
    "short/cockroachdb-deploy-init-settings-audit-concat-blind.yaml",
    "short/cockroachdb-deploy-init-settings-audit-concat-stateful.yaml",
    "short/cockroachdb-deploy-init-settings-audit.yaml",
    "short/cockroachdb-health-settings-recovery.yaml",
    "short/cockroachdb-scale-down-node-adversary.yaml",
    "short/cockroachdb-storage-lifecycle.yaml",
    "short/cockroachdb-throttle-rebalance-adversary.yaml",
    "short/cockroachdb-version-decommission.yaml",
    "short/cockroachdb-zone-rebalance-upgrade.yaml",
    "short/elasticsearch-certs-password-users.yaml",
    "short/elasticsearch-deploy-scale-transform-snapshot-concat-blind.yaml",
    "short/elasticsearch-deploy-scale-transform-snapshot-concat-stateful.yaml",
    "short/elasticsearch-deploy-scale-transform-snapshot.yaml",
    "short/elasticsearch-discovery-drift-adversary.yaml",
    "short/elasticsearch-http-selector-adversary.yaml",
    "short/elasticsearch-ingress-service-drift.yaml",
    "short/elasticsearch-master-downscale-sweep.yaml",
    "short/elasticsearch-monitoring-upgrade-certs-ca-trust.yaml",
    "short/elasticsearch-password-corruption-adversary.yaml",
    "short/elasticsearch-seed-hosts-shard-recovery.yaml",
    "short/elasticsearch-snapshot-secret-adversary.yaml",
    "short/elasticsearch-statefulset-down-adversary.yaml",
    "short/elasticsearch-transport-block-adversary.yaml",
    "short/mongodb-config-probe-recovery.yaml",
    "short/mongodb-configmap-delete-repair.yaml",
    "short/mongodb-deploy-init-users-audit-concat-blind.yaml",
    "short/mongodb-deploy-init-users-audit-concat-stateful.yaml",
    "short/mongodb-deploy-init-users-audit.yaml",
    "short/mongodb-external-roles-hardening.yaml",
    "short/mongodb-network-scale-incident.yaml",
    "short/mongodb-primary-stepdown-adversary.yaml",
    "short/mongodb-readiness-adversary.yaml",
    "short/mongodb-scale-monitor-decommission.yaml",
    "short/mongodb-secret-corruption-adversary.yaml",
    "short/mongodb-statefulset-scaling-sweep.yaml",
    "short/mongodb-tls-cert-password.yaml",
    "short/mongodb-upgrade-monitoring-hardening.yaml",
    "short/nginx-canary-rollback-audit.yaml",
    "short/nginx-class-upgrade-ratelimit.yaml",
    "short/nginx-configmap-corrupt-adversary.yaml",
    "short/nginx-create-tls-canary-concat-blind.yaml",
    "short/nginx-create-tls-canary-concat-stateful.yaml",
    "short/nginx-create-tls-canary.yaml",
    "short/nginx-delete-tls-adversary.yaml",
    "short/nginx-multihost-security-sweep.yaml",
    "short/nginx-ratelimit-otel-hard.yaml",
    "short/nginx-scale-down-backend-adversary.yaml",
    "short/nginx-strip-ratelimit-adversary.yaml",
    "short/platform-audit-only-chain.yaml",
    "short/platform-change-plan-compliance.yaml",
    "short/platform-compute-analytics-a.yaml",
    "short/platform-compute-analytics-b.yaml",
    "short/platform-compute-dual-adversary.yaml",
    "short/platform-data-dual-adversary.yaml",
    "short/platform-data-tier-hardening-a.yaml",
    "short/platform-data-tier-hardening-b.yaml",
    "short/platform-edge-search-a.yaml",
    "short/platform-edge-search-b.yaml",
    "short/platform-incident-response-a.yaml",
    "short/platform-incident-response-b.yaml",
    "short/platform-ingress-rabbit-dual-adversary.yaml",
    "short/platform-message-store-a.yaml",
    "short/platform-message-store-b.yaml",
    "short/platform-mongo-rabbit-dual-adversary.yaml",
    "short/platform-observability-a.yaml",
    "short/platform-rollback-rehearsal-chain.yaml",
    "short/platform-search-dual-adversary.yaml",
    "short/platform-security-audit-a.yaml",
    "short/rabbitmq-backup-upgrade-failover.yaml",
    "short/rabbitmq-blue-green-audit.yaml",
    "short/rabbitmq-clear-policy-failover.yaml",
    "short/rabbitmq-network-policy-recovery.yaml",
    "short/rabbitmq-policy-permission-audit-concat-blind.yaml",
    "short/rabbitmq-policy-permission-audit-concat-stateful.yaml",
    "short/rabbitmq-policy-permission-audit.yaml",
    "short/rabbitmq-queue-delete-drill.yaml",
    "short/rabbitmq-revoke-permission-incident.yaml",
    "short/rabbitmq-scale-down-recovery.yaml",
    "short/rabbitmq-tls-monitoring-drill.yaml",
    "short/ray-dashboard-job-chain-concat-blind.yaml",
    "short/ray-dashboard-job-chain-concat-stateful.yaml",
    "short/ray-dashboard-job-chain.yaml",
    "short/ray-full-observability-chain.yaml",
    "short/ray-gcs-block-adversary.yaml",
    "short/ray-head-service-delete-adversary.yaml",
    "short/ray-recovery-after-scale.yaml",
    "short/ray-scale-down-workers-adversary.yaml",
    "short/ray-scale-upgrade-teardown.yaml",
    "short/ray-worker-image-drift-adversary.yaml",
    "short/spark-executor-memory-drop-adversary.yaml",
    "short/spark-history-pvc-adversary.yaml",
    "short/spark-image-drift-adversary.yaml",
    "short/spark-pi-runtime-ops-concat-blind.yaml",
    "short/spark-pi-runtime-ops-concat-stateful.yaml",
    "short/spark-pi-runtime-ops.yaml",
    "short/spark-rbac-revoke-adversary.yaml",
    "short/spark-runtime-multitenant-streaming.yaml",
    "short/spark-secret-expire-adversary.yaml",
    "short/spark-skew-etl-oom-recovery.yaml",
    "short/spark-worker-scale-down-adversary.yaml",
)

_ELASTICSEARCH_STANDALONE_FIXTURE_CASES = {
    # These cases create distinct Elasticsearch topologies/resources in their
    # fresh precondition path. When they are placed after another ES stage, the
    # fresh path is skipped and their oracle can end up validating the wrong
    # cluster shape.
    "internal-http-service-drift",
    "transform-job-recovery",
}


def _load_added_workflow(rel_path: str) -> tuple[dict, list[dict]]:
    path = _WORKFLOWS_DIR / rel_path
    raw = load_workflow_file(path)
    workflow = normalize_workflow(raw, resources_dir=_RESOURCES_DIR)
    rows = resolve_workflow_rows(workflow, resources_dir=_RESOURCES_DIR)
    return workflow, rows


@pytest.mark.skipif(
    not _RESOURCES_DIR.exists() or not _WORKFLOWS_DIR.exists(),
    reason="cases/ or workflows/ directory not present in this environment",
)
@pytest.mark.parametrize("rel_path", ADDED_WORKFLOW_PATHS)
def test_added_workflow_resolves_cases_params_and_adversaries(rel_path: str):
    path = _WORKFLOWS_DIR / rel_path
    assert path.exists(), f"missing workflow file: {rel_path}"

    workflow, rows = _load_added_workflow(rel_path)
    stage_ids = [str(stage.get("id") or "") for stage in workflow["stages"]]
    stage_order = {stage_id: i for i, stage_id in enumerate(stage_ids)}

    assert workflow["id"] == path.stem
    assert len(rows) == len(workflow["stages"])
    assert len(stage_ids) == len(set(stage_ids))

    for row in rows:
        assert row["case"]["service"] == row["service"]
        assert row["case"]["case_name"] == row["case_name"]
        assert row["case"]["warnings"] == []

    for adversary in workflow.get("adversary") or []:
        inject_at = adversary.get("inject_at_stage")
        lift_at = adversary.get("lift_at_stage")

        assert inject_at in stage_order
        if lift_at:
            assert lift_at in stage_order
            assert stage_order[inject_at] < stage_order[lift_at]


@pytest.mark.skipif(
    not _RESOURCES_DIR.exists() or not _WORKFLOWS_DIR.exists(),
    reason="cases/ or workflows/ directory not present in this environment",
)
def test_added_workflows_are_visible_and_ok_in_ui_catalog():
    catalog = list_workflow_files(_WORKFLOWS_DIR, _RESOURCES_DIR)
    by_name = {entry["name"]: entry for entry in catalog}

    missing = [name for name in ADDED_WORKFLOW_PATHS if name not in by_name]
    invalid = [
        (name, by_name[name].get("errors") or [])
        for name in ADDED_WORKFLOW_PATHS
        if name in by_name and not by_name[name].get("ok")
    ]

    assert missing == []
    assert invalid == []


@pytest.mark.skipif(
    not _RESOURCES_DIR.exists() or not _WORKFLOWS_DIR.exists(),
    reason="cases/ or workflows/ directory not present in this environment",
)
@pytest.mark.parametrize("rel_path", ADDED_WORKFLOW_PATHS)
def test_added_workflows_have_supported_stage_composition(rel_path: str):
    workflow, _rows = _load_added_workflow(rel_path)
    stages = workflow["stages"]

    cockroach_cases = [
        stage["case_name"] for stage in stages if stage["service"] == "cockroachdb"
    ]
    if "decommission" in cockroach_cases:
        assert cockroach_cases[0] == "decommission"

    elasticsearch_stages = [
        stage for stage in stages if stage["service"] == "elasticsearch"
    ]
    elasticsearch_cases = [stage["case_name"] for stage in elasticsearch_stages]

    for case_name in _ELASTICSEARCH_STANDALONE_FIXTURE_CASES:
        if case_name in elasticsearch_cases:
            assert elasticsearch_cases[0] == case_name

    for index, stage in enumerate(elasticsearch_stages):
        if stage["case_name"] != "full-restart-upgrade-ha":
            continue
        previous_cases = set(elasticsearch_cases[:index])
        if "seed-hosts-repair" in previous_cases:
            assert stage.get("param_overrides", {}).get("expected_doc_count") == 4
