#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


SMOKE_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "resources"
WORKFLOW_DIR = SMOKE_DIR / "generated"
PROFILE = SMOKE_DIR / "local_workflow_profile.yaml"
AGENT = SMOKE_DIR / "repeat_stage_agent.py"
SUMMARY = SMOKE_DIR / "repeat_matrix_summary.json"
PYTHON = ROOT / ".venv" / "bin" / "python"

APPLY_RE = re.compile(r"PRECONDITION ([^:]+): not satisfied")
SATISFIED_RE = re.compile(r"PRECONDITION ([^:]+): already satisfied")

CASE_HINTS = {
    "cockroachdb/certificate-rotation": {
        "solver": "cockroach_certificate_rotation",
    },
    "cockroachdb/cluster-settings": {
        "solver": "cockroach_cluster_settings",
    },
    "cockroachdb/decommission": {
        "solver": "cockroach_decommission",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/deploy": {
        "solver": "cockroach_deploy",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/expose-ingress": {
        "solver": "cockroach_expose_ingress",
        "aliases": ["default", "ingress"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/generate-cert": {
        "solver": "cockroach_generate_cert",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/health-check-recovery": {
        "solver": "cockroach_health_check_recovery",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/initialize": {
        "solver": "cockroach_initialize",
    },
    "cockroachdb/major-upgrade-finalize": {
        "solver": "cockroach_major_upgrade_finalize",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/monitoring-integration": {
        "solver": "cockroach_monitoring",
        "aliases": ["monitoring"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/partitioned-update": {
        "solver": "cockroach_partitioned_update",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "cockroachdb/version-check": {
        "solver": "cockroach_version_check",
    },
    "cockroachdb/zone-config": {
        "solver": "cockroach_zone_config",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "demo/configmap-update": {
        "solver": "demo_configmap_update",
        "aliases": ["demo"],
    },
    "demo/configmap-update-two-ns": {
        "solver": "demo_configmap_update_two_ns",
        "aliases": ["source", "target"],
    },
    "elasticsearch/bootstrap-initial-master-nodes": {
        "solver": "elasticsearch_bootstrap_initial_master_nodes",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/deploy-core-cluster": {
        "solver": "elasticsearch_deploy_core_cluster",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/file-realm-user-roles-merge": {
        "solver": "elasticsearch_file_realm_user_roles_merge",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/full-restart-upgrade-ha": {
        "solver": "elasticsearch_full_restart_upgrade_ha",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/internal-http-service-drift": {
        "solver": "elasticsearch_internal_http_service_drift",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/master-downscale-voting-exclusions": {
        "solver": "elasticsearch_master_downscale_voting_exclusions",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/rotate-elastic-password": {
        "solver": "elasticsearch_rotate_elastic_password",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/rotate-http-certs": {
        "solver": "elasticsearch_rotate_http_certs",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/safe-downscale-with-shard-migration": {
        "solver": "elasticsearch_safe_downscale_with_shard_migration",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/scale-up-new-nodeset": {
        "solver": "elasticsearch_scale_up_new_nodeset",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/secure-http-ingress": {
        "solver": "elasticsearch_secure_http_ingress",
        "aliases": ["default", "ingress"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/seed-hosts-repair": {
        "solver": "elasticsearch_seed_hosts_repair",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/snapshot-repo-setup": {
        "solver": "elasticsearch_snapshot_repo_setup",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/stack-monitoring-sidecars": {
        "solver": "elasticsearch_stack_monitoring_sidecars",
        "aliases": ["monitoring"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/transform-job-recovery": {
        "solver": "elasticsearch_transform_job_recovery",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "elasticsearch/transport-additional-ca-trust": {
        "solver": "elasticsearch_transport_additional_ca_trust",
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/deploy": {
        "solver": "mongodb_deploy",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
        "expectation": "expected_reset",
        "note": "Bootstrap/reset case; stage 2 setup is expected to re-empty owned state.",
    },
    "mongodb/arbiters": {
        "solver": "mongodb_arbiters",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/custom-roles": {
        "solver": "mongodb_custom_roles",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/decommission": {
        "solver": "mongodb_decommission",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/external-access-horizons": {
        "solver": "mongodb_external_access_horizons",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/health-check-recovery": {
        "solver": "mongodb_health_check_recovery",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/initialize": {
        "solver": "mongodb_initialize",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/manual-rbac-reset": {
        "solver": "mongodb_manual_rbac_reset",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/mongod-config-update": {
        "solver": "mongodb_mongod_config_update",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/monitoring-integration": {
        "solver": "mongodb_monitoring_integration",
        "aliases": ["mongodb", "monitoring"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/password-rotation": {
        "solver": "mongodb_password_rotation",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/readiness-probe-tuning": {
        "solver": "mongodb_readiness_probe_tuning",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/replica-scaling": {
        "solver": "mongodb_replica_scaling",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/setup-rbac-drift-app": {
        "solver": "mongodb_setup_rbac_drift_app",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/setup-rbac-drift-reporting": {
        "solver": "mongodb_setup_rbac_drift_reporting",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/statefulset-customization": {
        "solver": "mongodb_statefulset_customization",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/tls-setup": {
        "solver": "mongodb_tls_setup",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/user-management": {
        "solver": "mongodb_user_management",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/version-upgrade": {
        "solver": "mongodb_version_upgrade",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "mongodb/certificate-rotation": {
        "solver": "mongodb_certificate_rotation",
        "aliases": ["mongodb"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "nginx-ingress/header_canary_routing": {
        "solver": "nginx_canary",
        "aliases": ["app", "ingress"],
    },
    "nginx-ingress/https_ingress_ready": {
        "solver": "nginx_https",
        "aliases": ["app", "ingress"],
    },
    "nginx-ingress/ingress_class_routing": {
        "solver": "nginx_class_routing",
        "aliases": ["app", "ingress"],
    },
    "nginx-ingress/ingress_route_ready": {
        "solver": "nginx_route",
        "aliases": ["app", "ingress"],
    },
    "nginx-ingress/otel_ingress_logging_ready": {
        "solver": "nginx_otel",
        "aliases": ["app", "ingress", "otel"],
    },
    "nginx-ingress/rate_limit_ingress": {
        "solver": "nginx_rate_limit",
        "aliases": ["app", "ingress"],
    },
    "rabbitmq-experiments/blue_green_migration": {
        "solver": "rabbitmq_blue_green_migration",
        "aliases": ["source", "target"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "rabbitmq-experiments/classic_queue": {
        "solver": "rabbitmq_classic_queue",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "900", "verify": "600", "cleanup": "600"},
    },
    "rabbitmq-experiments/failover": {
        "solver": "rabbitmq_failover",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "rabbitmq-experiments/manual_backup_restore": {
        "solver": "rabbitmq_manual_backup_restore",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "rabbitmq-experiments/manual_monitoring": {
        "solver": "rabbitmq_manual_monitoring",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "900", "verify": "600", "cleanup": "600"},
    },
    "rabbitmq-experiments/manual_policy_sync": {
        "solver": "rabbitmq_manual_policy_sync",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "900", "verify": "600", "cleanup": "600"},
    },
    "rabbitmq-experiments/manual_runtime_reset": {
        "solver": "rabbitmq_manual_runtime_reset",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "900", "verify": "600", "cleanup": "600"},
    },
    "rabbitmq-experiments/manual_skip_upgrade": {
        "solver": "rabbitmq_manual_skip_upgrade",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "rabbitmq-experiments/manual_tls_rotation": {
        "solver": "rabbitmq_manual_tls_rotation",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "1200", "verify": "900", "cleanup": "900"},
    },
    "rabbitmq-experiments/manual_user_permission": {
        "solver": "rabbitmq_manual_user_permission",
        "aliases": ["rabbitmq"],
        "timeouts": {"setup": "900", "verify": "600", "cleanup": "600"},
    },
    "ray/cluster_ready": {
        "solver": "noop",
        "aliases": ["ray"],
    },
    "ray/cluster_teardown": {
        "solver": "ray_cluster_teardown",
        "aliases": ["ray"],
    },
    "ray/dashboard_reachable": {
        "solver": "ray_dashboard",
        "aliases": ["ray"],
    },
    "ray/job_execution": {
        "solver": "ray_job_execution",
        "aliases": ["ray"],
    },
    "ray/version_upgrade": {
        "solver": "ray_version_upgrade",
        "aliases": ["ray"],
    },
    "ray/worker_scaling": {
        "solver": "ray_worker_scale",
        "aliases": ["ray"],
    },
    "spark/spark_etl_pipeline_completion": {
        "solver": "spark_etl",
        "aliases": ["spark"],
    },
    "spark/spark_history_server_ready": {
        "solver": "spark_history",
        "aliases": ["spark"],
    },
    "spark/spark_multi_tenant_job_execution": {
        "solver": "spark_multi_tenant",
        "aliases": ["team_a", "team_b"],
    },
    "spark/spark_pi_job_execution": {
        "solver": "spark_pi",
        "aliases": ["spark"],
    },
    "spark/spark_runtime_bundle_ready": {
        "solver": "spark_runtime_bundle",
        "aliases": ["spark"],
    },
    "spark/spark_sql_job_execution": {
        "solver": "spark_sql",
        "aliases": ["spark"],
    },
    "spark/spark_worker_scaling": {
        "solver": "spark_worker_scale",
        "aliases": ["spark"],
    },
}


def discover_active_cases() -> list[dict]:
    cases = []
    for service_dir in sorted(RESOURCES.iterdir()):
        if not service_dir.is_dir() or service_dir.name == "legacy":
            continue
        for case_dir in sorted(service_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            test_path = case_dir / "test.yaml"
            resource_dir = case_dir / "resource"
            if not test_path.is_file() or not resource_dir.is_dir():
                continue
            key = f"{service_dir.name}/{case_dir.name}"
            hint = dict(CASE_HINTS.get(key) or {})
            aliases = list(hint.get("aliases") or [service_dir.name])
            cases.append(
                {
                    "key": key,
                    "service": service_dir.name,
                    "case": case_dir.name,
                    "solver": hint.get("solver"),
                    "aliases": aliases,
                    "timeouts": dict(hint.get("timeouts") or {}),
                    "expectation": hint.get("expectation"),
                    "note": hint.get("note"),
                }
            )
    return cases


def _parse_payload(stdout: str):
    marker = "[\n  {"
    pos = stdout.find(marker)
    if pos < 0:
        marker = "[{"
        pos = stdout.find(marker)
    if pos < 0:
        raise RuntimeError(stdout)
    return json.loads(stdout[pos:])


def _profile_sandbox() -> str:
    try:
        import yaml

        data = yaml.safe_load(PROFILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    return str(data.get("sandbox") or "").strip().lower()


def _resolve_direct_proxy_server(source_kubeconfig: str) -> str:
    env = os.environ.copy()
    if source_kubeconfig:
        env["KUBECONFIG"] = source_kubeconfig
    try:
        raw = subprocess.check_output(
            ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
            text=True,
            env=env,
        ).strip()
    except Exception:
        return ""
    if not raw:
        return ""
    raw = raw.replace("https://", "").replace("http://", "")
    return raw.split("/", 1)[0]


def _launcher_env_and_args() -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    extra_args: list[str] = []
    source_kubeconfig = (env.get("KUBECONFIG") or "").strip()
    proxy_server = (env.get("BENCHMARK_PROXY_SERVER") or "").strip()
    if not proxy_server and _profile_sandbox() == "local":
        proxy_server = _resolve_direct_proxy_server(source_kubeconfig)
    if source_kubeconfig:
        extra_args.extend(["--source-kubeconfig", source_kubeconfig])
    if proxy_server:
        extra_args.extend(["--proxy-server", proxy_server])
    return env, extra_args


def _workflow_path(case_entry: dict) -> Path:
    slug = f"{case_entry['service']}-{case_entry['case']}".replace("/", "-")
    return WORKFLOW_DIR / f"repeat_{slug}.json"


def _write_workflow(case_entry: dict) -> Path:
    path = _workflow_path(case_entry)
    workflow = {
        "apiVersion": "benchmark/v1",
        "kind": "Workflow",
        "metadata": {"name": f"repeat-{case_entry['service']}-{case_entry['case']}"},
        "spec": {
            "prompt_mode": "progressive",
            "stages": [
                {
                    "id": "stage_first",
                    "service": case_entry["service"],
                    "case": case_entry["case"],
                    "namespaces": case_entry["aliases"],
                },
                {
                    "id": "stage_second",
                    "service": case_entry["service"],
                    "case": case_entry["case"],
                    "namespaces": case_entry["aliases"],
                },
            ],
        },
    }
    if case_entry["aliases"]:
        workflow["spec"]["namespaces"] = list(case_entry["aliases"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    return path


def _default_timeouts(case_entry: dict) -> dict[str, str]:
    merged = {"submit": "900", "setup": "600", "verify": "600", "cleanup": "600"}
    merged.update(case_entry.get("timeouts") or {})
    return merged


def _stage_two_dir(abs_run_dir: Path) -> Path | None:
    stage_root = abs_run_dir / "stage_runs"
    if not stage_root.is_dir():
        return None
    candidates = sorted(stage_root.glob("02_*"))
    if not candidates:
        return None
    return candidates[0]


def _read_stage_two_preop(abs_run_dir: Path) -> dict:
    out = {}
    stage_dir = _stage_two_dir(abs_run_dir)
    if not stage_dir:
        return out
    preop = stage_dir / "preoperation.log"
    out["second_stage_run_dir"] = str(stage_dir)
    out["second_preoperation_log"] = str(preop)
    if not preop.exists():
        return out
    text = preop.read_text(encoding="utf-8")
    apply_hits = APPLY_RE.findall(text)
    satisfied_hits = SATISFIED_RE.findall(text)
    out["second_stage_apply_units"] = apply_hits
    out["second_stage_had_apply"] = bool(apply_hits)
    out["second_stage_satisfied_units"] = satisfied_hits
    out["second_stage_preoperation_excerpt"] = "\n".join(text.splitlines()[:80])
    return out


def _classify(row: dict) -> str:
    if not row.get("solver"):
        return "no_smoke_solver"
    if row.get("returncode") != 0:
        return "runner_error"
    status = str((row.get("result") or {}).get("status") or "").strip().lower()
    if status != "passed":
        if row.get("expectation") == "environment_blocked":
            return "environment_blocked"
        if row.get("second_stage_had_apply"):
            return "carryover_failed"
        return "failed"
    if row.get("expectation") == "expected_reset":
        return "expected_reset"
    if row.get("second_stage_had_apply"):
        return "unexpected_mutation"
    return "ideal_noop"


def _run_case(case_entry: dict) -> dict:
    row = {
        "key": case_entry["key"],
        "service": case_entry["service"],
        "case": case_entry["case"],
        "solver": case_entry.get("solver"),
        "aliases": list(case_entry.get("aliases") or []),
        "expectation": case_entry.get("expectation"),
        "note": case_entry.get("note"),
    }
    if not case_entry.get("solver"):
        row["classification"] = _classify(row)
        return row

    wf_path = _write_workflow(case_entry)
    timeouts = _default_timeouts(case_entry)
    python_exec = PYTHON if PYTHON.exists() else Path(sys.executable)
    env, launcher_args = _launcher_env_and_args()
    cmd = [
        str(python_exec),
        "orchestrator.py",
        "workflow-run",
        "--profile",
        str(PROFILE),
        "--workflow",
        str(wf_path),
        "--agent-cmd",
        f"python3 {AGENT} --solver {case_entry['solver']}",
        "--submit-timeout",
        timeouts["submit"],
        "--setup-timeout",
        timeouts["setup"],
        "--setup-timeout-mode",
        "auto",
        "--verify-timeout",
        timeouts["verify"],
        "--cleanup-timeout",
        timeouts["cleanup"],
        "--max-attempts",
        "1",
        "--stage-failure-mode",
        "terminate",
    ]
    cmd.extend(launcher_args)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    row["command"] = cmd
    row["returncode"] = proc.returncode
    row["stdout_tail"] = "\n".join(proc.stdout.splitlines()[-80:])

    if proc.returncode == 0:
        payload = _parse_payload(proc.stdout)
        result = (payload[0] or {}).get("result") or {}
        row["result"] = result
        run_dir = result.get("run_dir")
        if run_dir:
            abs_run_dir = ROOT / run_dir
            row["abs_run_dir"] = str(abs_run_dir)
            row.update(_read_stage_two_preop(abs_run_dir))

    row["classification"] = _classify(row)
    return row


def _selected_cases(filters: list[str]) -> list[dict]:
    all_cases = discover_active_cases()
    if not filters:
        return all_cases
    wanted = set(filters)
    selected = []
    for case in all_cases:
        service_case = case["key"]
        if case["case"] in wanted or case["service"] in wanted or service_case in wanted:
            selected.append(case)
    return selected


def _write_summary(rows: list[dict]) -> None:
    counts = Counter(row.get("classification") or "unknown" for row in rows)
    payload = {
        "total_cases": len(rows),
        "runnable_cases": sum(1 for row in rows if row.get("solver")),
        "classification_counts": dict(sorted(counts.items())),
        "cases": rows,
    }
    SUMMARY.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filters", nargs="*")
    parser.add_argument("--runnable-only", action="store_true")
    args = parser.parse_args()

    selected = _selected_cases(args.filters)
    if args.filters and not selected:
        print(f"no cases matched filters: {args.filters}", file=sys.stderr)
        return 1
    if args.runnable_only:
        selected = [case for case in selected if case.get("solver")]

    out = []
    for case in selected:
        print(f"[repeat-matrix] running {case['key']}", flush=True)
        row = _run_case(case)
        out.append(row)
        _write_summary(out)
        classification = row.get("classification")
        if classification == "no_smoke_solver":
            print(f"[repeat-matrix] skipped {case['key']} (no smoke solver)", flush=True)
        elif row.get("returncode") != 0:
            print(f"[repeat-matrix] runner error for {case['key']}", flush=True)
        elif classification == "environment_blocked":
            print(f"[repeat-matrix] environment blocked {case['key']}", flush=True)
        elif classification == "unexpected_mutation":
            print(
                f"[repeat-matrix] stage2 apply {case['key']} -> {row.get('second_stage_apply_units')}",
                flush=True,
            )
        else:
            print(f"[repeat-matrix] {case['key']} -> {classification}", flush=True)

    _write_summary(out)
    print(json.dumps({"total_cases": len(out), "cases": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
