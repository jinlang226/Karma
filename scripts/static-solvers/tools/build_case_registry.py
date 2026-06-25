#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

from _shared import (
    CASES_DIR,
    REGISTRY_DIR,
    STATIC_ROOT,
    VENDOR_ROOT,
    now_utc_iso,
    slugify_function_name,
    write_yaml,
)


RESOURCE_TABLE_RE = re.compile(
    r"^\| `([^`]+)` \| \[`([^`]+)`\]\([^)]+\) \| ([^|]+) \| (.*) \|$"
)


CURRENT_CASE_ALIAS_MAP: dict[tuple[str, str], dict[str, str]] = {
    ("nginx-ingress", "class_only_upgrade"): {
        "imported_case": "nginx-ingress/ingress_class_routing",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "create_ingress"): {
        "imported_case": "nginx-ingress/ingress_route_ready",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "ingress_canary"): {
        "imported_case": "nginx-ingress/header_canary_routing",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "otel_log_format"): {
        "imported_case": "nginx-ingress/otel_ingress_logging_ready",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "rate_limit_ingress_easy"): {
        "imported_case": "nginx-ingress/rate_limit_ingress",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "rate_limit_replica_hard"): {
        "imported_case": "nginx-ingress/rate_limit_ingress",
        "strategy": "shell_wrapper_variant",
    },
    ("nginx-ingress", "renew_tls_secret"): {
        "imported_case": "nginx-ingress/https_ingress_ready",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "dashboard_exposure"): {
        "imported_case": "ray/dashboard_reachable",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "deploy_cluster"): {
        "imported_case": "ray/cluster_ready",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "job_submission"): {
        "imported_case": "ray/job_execution",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "scale_workers"): {
        "imported_case": "ray/worker_scaling",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "teardown_cluster"): {
        "imported_case": "ray/cluster_teardown",
        "strategy": "shell_wrapper_variant",
    },
    ("ray", "upgrade_version"): {
        "imported_case": "ray/version_upgrade",
        "strategy": "shell_wrapper_variant",
    },
    ("spark", "deploy_spark_pi"): {
        "imported_case": "spark/spark_pi_job_execution",
        "strategy": "shell_wrapper_variant",
    },
    ("spark", "spark_etl_skew_oom"): {
        "imported_case": "spark/spark_etl_pipeline_completion",
        "strategy": "shell_wrapper_variant",
    },
    ("spark", "spark_multi_tenant"): {
        "imported_case": "spark/spark_multi_tenant_job_execution",
        "strategy": "shell_wrapper_variant",
    },
    ("spark", "spark_runtime_ops"): {
        "imported_case": "spark/spark_runtime_bundle_ready",
        "strategy": "shell_wrapper_variant",
    },
    ("spark", "spark_streaming_autoscale"): {
        "imported_case": "spark/spark_worker_scaling",
        "strategy": "shell_wrapper_variant",
    },
    ("rabbitmq", "blue_green_migration"): {
        "imported_case": "rabbitmq-experiments/blue_green_migration",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "classic_queue"): {
        "imported_case": "rabbitmq-experiments/classic_queue",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "failover"): {
        "imported_case": "rabbitmq-experiments/failover",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "manual_backup_restore"): {
        "imported_case": "rabbitmq-experiments/manual_backup_restore",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "manual_monitoring"): {
        "imported_case": "rabbitmq-experiments/manual_monitoring",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "manual_policy_sync"): {
        "imported_case": "rabbitmq-experiments/manual_policy_sync",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "manual_skip_upgrade"): {
        "imported_case": "rabbitmq-experiments/manual_skip_upgrade",
        "strategy": "python_wrapper",
    },
    ("rabbitmq", "manual_tls_rotation"): {
        "imported_case": "rabbitmq-experiments/manual_tls_rotation",
        "strategy": "shell_wrapper_variant",
    },
    ("rabbitmq", "manual_user_permission"): {
        "imported_case": "rabbitmq-experiments/manual_user_permission",
        "strategy": "python_wrapper",
    },
    ("mongodb", "version-upgrade-hard"): {
        "imported_case": "mongodb/version-upgrade",
        "strategy": "shell_wrapper_variant",
    },
    ("elasticsearch", "full-restart-upgrade-ha-hard"): {
        "imported_case": "elasticsearch/full-restart-upgrade-ha",
        "strategy": "shell_wrapper_variant",
    },
}


SUBMIT_ONLY_CASES = {
    ("cockroachdb", "change-plan-only"),
    ("cockroachdb", "readonly-audit"),
    ("cockroachdb", "rollback-rehearsal"),
    ("elasticsearch", "change-plan-only"),
    ("elasticsearch", "readonly-audit"),
    ("elasticsearch", "rollback-rehearsal"),
    ("mongodb", "change-plan-only"),
    ("mongodb", "readonly-audit"),
    ("mongodb", "rollback-rehearsal"),
    ("nginx-ingress", "change-plan-only"),
    ("nginx-ingress", "readonly-audit"),
    ("nginx-ingress", "rollback-rehearsal"),
    ("rabbitmq", "change-plan-only"),
    ("rabbitmq", "readonly-audit"),
    ("rabbitmq", "rollback-rehearsal"),
    ("ray", "change-plan-only"),
    ("ray", "readonly-audit"),
    ("ray", "rollback-rehearsal"),
    ("spark", "change-plan-only"),
    ("spark", "readonly-audit"),
    ("spark", "rollback-rehearsal"),
}


UNSUPPORTED_CASES = {
    ("ray", "worker_recovery"),
    ("spark", "spark_data_skew"),
}


def _parse_imported_mapping() -> dict[str, dict[str, str]]:
    readme_path = VENDOR_ROOT / "scripts" / "resource-solvers" / "README.md"
    lines = readme_path.read_text().splitlines()
    cases: dict[str, dict[str, str]] = {}
    for line in lines:
        match = RESOURCE_TABLE_RE.match(line.strip())
        if not match:
            continue
        resource_case, solver_script, provenance, notes = match.groups()
        solver_path = (
            VENDOR_ROOT
            / "scripts"
            / "resource-solvers"
            / "solvers"
            / solver_script
        )
        cases[resource_case] = {
            "resource_case": resource_case,
            "solver_script": solver_script,
            "solver_path": solver_path.relative_to(STATIC_ROOT).as_posix(),
            "provenance": provenance.strip(),
            "notes": notes.strip(),
        }
    return cases


def _current_cases() -> list[tuple[str, str]]:
    items = []
    for test_file in sorted(CASES_DIR.glob("*/*/test.yaml")):
        items.append((test_file.parent.parent.name, test_file.parent.name))
    return items


def _build_current_record(
    service: str,
    case_name: str,
    imported_cases: dict[str, dict[str, str]],
) -> dict[str, str]:
    key = (service, case_name)
    direct_imported_case = f"{service}/{case_name}"

    if key in SUBMIT_ONLY_CASES:
        return {
            "service": service,
            "case_name": case_name,
            "status": "candidate",
            "strategy": "submit_only_candidate",
            "function_name": slugify_function_name(service, case_name),
            "imported_case": "",
            "solver_path": "",
            "notes": "Static no-op submit candidate; requires runtime validation.",
        }

    if key in UNSUPPORTED_CASES:
        return {
            "service": service,
            "case_name": case_name,
            "status": "unsupported",
            "strategy": "unsupported",
            "function_name": slugify_function_name(service, case_name),
            "imported_case": "",
            "solver_path": "",
            "notes": "No safe reused solver chosen yet.",
        }

    if direct_imported_case in imported_cases:
        imported = imported_cases[direct_imported_case]
        notes = imported["notes"]
        status = "review_required" if "Reference only" in notes else "candidate"
        strategy = "direct_shell"
        if service == "rabbitmq":
            strategy = "python_wrapper" if case_name != "manual_tls_rotation" else "direct_shell"
        return {
            "service": service,
            "case_name": case_name,
            "status": status,
            "strategy": strategy,
            "function_name": slugify_function_name(service, case_name),
            "imported_case": direct_imported_case,
            "solver_path": imported["solver_path"],
            "notes": notes,
        }

    alias = CURRENT_CASE_ALIAS_MAP.get(key)
    if alias:
        imported_case = alias["imported_case"]
        imported = imported_cases.get(imported_case)
        if imported is None:
            return {
                "service": service,
                "case_name": case_name,
                "status": "unsupported",
                "strategy": "unsupported",
                "function_name": slugify_function_name(service, case_name),
                "imported_case": imported_case,
                "solver_path": "",
                "notes": "Alias points to missing imported case.",
            }
        notes = imported["notes"]
        status = "review_required" if "Reference only" in notes else "candidate"
        return {
            "service": service,
            "case_name": case_name,
            "status": status,
            "strategy": alias["strategy"],
            "function_name": slugify_function_name(service, case_name),
            "imported_case": imported_case,
            "solver_path": imported["solver_path"],
            "notes": notes,
        }

    return {
        "service": service,
        "case_name": case_name,
        "status": "unsupported",
        "strategy": "unsupported",
        "function_name": slugify_function_name(service, case_name),
        "imported_case": "",
        "solver_path": "",
        "notes": "No confident imported solver mapping.",
    }


def main() -> int:
    imported_cases = _parse_imported_mapping()
    imported_payload = {
        "version": 1,
        "source_branch": "import-improve-resources",
        "generated_at": now_utc_iso(),
        "cases": list(imported_cases.values()),
    }
    current_records = [
        _build_current_record(service, case_name, imported_cases)
        for service, case_name in _current_cases()
    ]
    current_payload = {
        "version": 1,
        "generated_at": now_utc_iso(),
        "cases": current_records,
    }
    write_yaml(REGISTRY_DIR / "imported_resource_case_map.yaml", imported_payload)
    write_yaml(REGISTRY_DIR / "current_case_map.yaml", current_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
