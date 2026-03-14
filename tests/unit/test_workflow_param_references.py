from app.workflow import parse_stage_param_reference, resolve_stage_param_overrides


def test_parse_stage_param_reference_matches_expected_syntax():
    ref = parse_stage_param_reference("${stages.stage_1.params.to_version}")
    assert ref == {"stage_id": "stage_1", "param": "to_version"}
    assert parse_stage_param_reference(" ${stages.s1.params.x} ") == {"stage_id": "s1", "param": "x"}
    assert parse_stage_param_reference("${stages.s1.params}") is None
    assert parse_stage_param_reference("${stages.s1.outputs.x}") is None
    assert parse_stage_param_reference(123) is None


def test_resolve_stage_param_overrides_replaces_reference_and_tracks_sources():
    stages = [
        {
            "id": "stage_1_upgrade_a",
            "namespaces": ["cluster_a"],
            "param_overrides": {"to_version": "3.7"},
        },
        {
            "id": "stage_2_restore_a",
            "namespaces": ["cluster_a"],
            "param_overrides": {
                "cluster_prefix": "rabbitmq-a",
                "version_hint": "${stages.stage_1_upgrade_a.params.to_version}",
            },
        },
    ]
    overrides, warnings, sources = resolve_stage_param_overrides(
        stage=stages[1],
        stage_index=1,
        all_stages=stages,
        prior_stage_params={"stage_1_upgrade_a": {"to_version": "3.7"}},
    )
    assert warnings == []
    assert overrides == {
        "cluster_prefix": "rabbitmq-a",
        "version_hint": "3.7",
    }
    assert sources["cluster_prefix"]["kind"] == "literal"
    assert sources["version_hint"] == {
        "kind": "stage_param_ref",
        "stage_id": "stage_1_upgrade_a",
        "param": "to_version",
        "expr": "${stages.stage_1_upgrade_a.params.to_version}",
    }


def test_resolve_stage_param_overrides_rejects_unknown_or_forward_refs():
    stages = [
        {"id": "s1", "param_overrides": {"x": "1"}},
        {"id": "s2", "param_overrides": {"y": "2"}},
    ]
    try:
        resolve_stage_param_overrides(
            stage={"id": "s2", "param_overrides": {"a": "${stages.missing.params.x}"}},
            stage_index=1,
            all_stages=stages,
            prior_stage_params={"s1": {"x": "1"}},
        )
        raise AssertionError("expected unknown stage reference error")
    except ValueError as exc:
        assert "unknown stage" in str(exc)

    try:
        resolve_stage_param_overrides(
            stage={"id": "s1", "param_overrides": {"a": "${stages.s2.params.y}"}},
            stage_index=0,
            all_stages=stages,
            prior_stage_params={},
        )
        raise AssertionError("expected forward reference error")
    except ValueError as exc:
        assert "earlier stage" in str(exc)

    try:
        resolve_stage_param_overrides(
            stage={"id": "s2", "param_overrides": {"a": "${stages.s1.params.missing}"}},
            stage_index=1,
            all_stages=stages,
            prior_stage_params={"s1": {"x": "1"}},
        )
        raise AssertionError("expected unknown param error")
    except ValueError as exc:
        assert "unknown param" in str(exc)


def test_resolve_stage_param_overrides_warns_on_potential_stale_reference():
    stages = [
        {
            "id": "stage_1_upgrade_a",
            "namespaces": ["cluster_a"],
            "param_overrides": {"to_version": "3.7"},
        },
        {
            "id": "stage_2_upgrade_a_again",
            "namespaces": ["cluster_a"],
            "param_overrides": {"to_version": "3.9"},
        },
        {
            "id": "stage_3_restore_a",
            "namespaces": ["cluster_a"],
            "param_overrides": {"version_hint": "${stages.stage_1_upgrade_a.params.to_version}"},
        },
    ]
    overrides, warnings, _sources = resolve_stage_param_overrides(
        stage=stages[2],
        stage_index=2,
        all_stages=stages,
        prior_stage_params={
            "stage_1_upgrade_a": {"to_version": "3.7"},
            "stage_2_upgrade_a_again": {"to_version": "3.9"},
        },
    )
    assert overrides["version_hint"] == "3.7"
    assert len(warnings) == 1
    assert "may be stale" in warnings[0]
    assert "stage_2_upgrade_a_again" in warnings[0]

