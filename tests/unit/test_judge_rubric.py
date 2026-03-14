from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

import app.judge.rubric as rubric_mod
from app.judge.rubric import load_merged_rubric


def _write_yaml(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_missing_overlay_is_optional_with_warning():
    warnings = []
    rubric = load_merged_rubric(
        service="rabbitmq-experiments",
        case="manual_monitoring",
        warnings=warnings,
    )
    assert rubric["rubric_id"]
    assert (rubric.get("questions") or [])
    source = rubric.get("source") or {}
    assert source.get("overlay_count") == 0
    assert source.get("overlays") == []
    assert not any("overlay" in warning for warning in warnings)


def test_rabbitmq_experiments_case_profiles_resolve():
    expected_profiles = {
        "blue_green_migration": "rabbitmq_data_plane_migration_v1",
        "classic_queue": "rabbitmq_data_plane_migration_v1",
        "failover": "rabbitmq_availability_recovery_v1",
        "manual_backup_restore": "rabbitmq_availability_recovery_v1",
        "manual_monitoring": "rabbitmq_observability_config_v1",
        "manual_policy_sync": "rabbitmq_observability_config_v1",
        "manual_skip_upgrade": "rabbitmq_upgrade_policy_v1",
        "manual_tls_rotation": "rabbitmq_security_tls_v1",
        "manual_user_permission": "rabbitmq_security_permissions_v1",
    }

    for case, expected_profile in expected_profiles.items():
        warnings = []
        rubric = load_merged_rubric(
            service="rabbitmq-experiments",
            case=case,
            warnings=warnings,
        )
        source = rubric.get("source") or {}
        assert source.get("profile") == expected_profile
        assert rubric.get("rubric_id") == f"rabbitmq-experiments.{case}.trajectory.v1"
        assert not any("profile '" in warning for warning in warnings)
        assert (rubric.get("questions") or [])


def test_hierarchical_overlay_merge_order_ignores_runtime_overlays():
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        resources_dir = temp_root / "resources"
        service = "svc"
        case = "case_a"

        _write_yaml(
            resources_dir / "judge_base.yaml",
            {
                "defaults": {
                    "rubric_id": "global-default",
                    "prompt_notes_extra": ["from-global-defaults"],
                },
                "profiles": {
                    "global_profile": {
                        "additional_questions": [
                            {
                                "id": "q_global_profile",
                                "track": "process_quality",
                                "weight": 0.3,
                                "prompt": "global profile question",
                            }
                        ]
                    }
                },
            },
        )
        _write_yaml(
            resources_dir / service / "judge_base.yaml",
            {
                "defaults": {
                    "rubric_version": "2",
                    "prompt_notes_extra": ["from-service-defaults"],
                },
                "profiles": {
                    "service_profile": {
                        "questions": [
                            {
                                "id": "q_service_profile",
                                "track": "process_quality",
                                "weight": 1,
                                "prompt": "service profile base question",
                            }
                        ]
                    }
                },
            },
        )
        _write_yaml(
            resources_dir / service / case / "test.yaml",
            {
                "type": "fixture",
                "judge": {
                    "profile": "service_profile",
                    "overrides": {
                        "additional_questions": [
                            {
                                "id": "q_case_override",
                                "track": "efficiency",
                                "weight": 0.5,
                                "prompt": "case override question",
                            }
                        ]
                    },
                },
            },
        )
        _write_yaml(
            resources_dir / service / case / "judge.yaml",
            {
                "overrides": {
                    "prompt_notes_extra": ["from-case-judge-yaml"],
                }
            },
        )
        with patch.object(rubric_mod, "RESOURCES_DIR", resources_dir):
            warnings = []
            rubric = load_merged_rubric(service, case, warnings)

        question_ids = [item.get("id") for item in rubric.get("questions") or []]
        assert question_ids == ["q_service_profile", "q_case_override"]

        prompt_notes = rubric.get("prompt_notes") or []
        assert "from-global-defaults" in prompt_notes
        assert "from-service-defaults" in prompt_notes
        assert "from-case-judge-yaml" in prompt_notes

        source = rubric.get("source") or {}
        assert source.get("profile") == "service_profile"
        layers = [item.get("layer") for item in source.get("layers") or []]
        assert layers == ["global_defaults", "service_defaults", "profile", "case_overrides"]
        assert source.get("overlay_count") == 0
        assert source.get("overlays") == []
        assert not warnings


def test_legacy_judge_base_is_still_supported():
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        resources_dir = temp_root / "resources"

        _write_yaml(
            resources_dir / "judge_base.yaml",
            {
                "rubric_id": "legacy-global-rubric",
                "objective_weights": {"process_quality": 0.6, "efficiency": 0.4},
            },
        )
        _write_yaml(resources_dir / "svc" / "case_a" / "test.yaml", {"type": "fixture"})

        with patch.object(rubric_mod, "RESOURCES_DIR", resources_dir):
            warnings = []
            rubric = load_merged_rubric("svc", "case_a", warnings)

        assert rubric.get("rubric_id") == "legacy-global-rubric"
        weights = rubric.get("objective_weights") or {}
        assert round(weights.get("process_quality", 0), 2) == 0.6
        assert round(weights.get("efficiency", 0), 2) == 0.4


def test_classifier_profile_and_case_additions_merge_deterministically():
    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        resources_dir = temp_root / "resources"
        service = "svc"
        case = "case_a"

        _write_yaml(
            resources_dir / "judge_base.yaml",
            {
                "profiles": {
                    "awareness_profile": {
                        "additional_classifiers": [
                            {
                                "id": "regression_awareness",
                                "labels": ["explicit", "implicit", "none", "unknown"],
                                "default_label": "none",
                                "rules": [
                                    {
                                        "id": "explicit_rule",
                                        "label": "explicit",
                                        "all": [
                                            {
                                                "ref": "q.awareness_explicit_intent.score",
                                                "op": "gte",
                                                "value": 3.5,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                }
            },
        )
        _write_yaml(
            resources_dir / service / case / "test.yaml",
            {
                "type": "fixture",
                "judge": {
                    "profile": "awareness_profile",
                    "overrides": {
                        "additional_classifiers": [
                            {
                                "id": "safety_behavior",
                                "labels": ["safe", "unsafe"],
                                "default_label": "unsafe",
                                "rules": [
                                    {
                                        "id": "safe_rule",
                                        "label": "safe",
                                        "all": [
                                            {
                                                "ref": "q.awareness_safe_behavior.score",
                                                "op": "gte",
                                                "value": 3.0,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                },
            },
        )

        with patch.object(rubric_mod, "RESOURCES_DIR", resources_dir):
            warnings = []
            rubric = load_merged_rubric(service, case, warnings)

        classifiers = rubric.get("classifiers") or []
        classifier_ids = {item.get("id") for item in classifiers}
        assert classifier_ids == {"regression_awareness", "safety_behavior"}
        assert not warnings
