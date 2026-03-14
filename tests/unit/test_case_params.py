from app.case_params import render_case_data_with_params, resolve_case_params


def test_resolve_case_params_defaults_and_overrides():
    case_data = {
        "params": {
            "definitions": {
                "replicas": {"type": "int", "default": 3, "min": 1, "max": 7},
                "phase": {"type": "string", "default": "alpha"},
            }
        },
        "detailedInstructions": "replicas={{params.replicas}} phase={{params.phase}}",
    }
    params, warnings = resolve_case_params(case_data, {"replicas": 5})
    assert params == {"replicas": 5, "phase": "alpha"}
    assert warnings == []

    rendered = render_case_data_with_params(case_data, params)
    assert rendered["detailedInstructions"] == "replicas=5 phase=alpha"


def test_resolve_case_params_raises_for_unknown_override():
    case_data = {
        "params": {"definitions": {"phase": {"type": "string", "default": "alpha"}}},
        "detailedInstructions": "phase={{params.phase}}",
    }
    try:
        resolve_case_params(case_data, {"unknown": "x"})
        assert False, "expected ValueError for unknown param override"
    except ValueError as exc:
        assert "unknown param_overrides keys" in str(exc)


def test_resolve_case_params_raises_for_undefined_template_token():
    case_data = {
        "params": {"definitions": {"phase": {"type": "string", "default": "alpha"}}},
        "detailedInstructions": "version={{params.target_version}}",
    }
    try:
        resolve_case_params(case_data, {})
        assert False, "expected ValueError for undefined token"
    except ValueError as exc:
        assert "undefined params referenced in templates" in str(exc)


def test_resolve_case_params_allows_unresolved_prompt_field_tokens_when_enabled():
    case_data = {
        "params": {"definitions": {"phase": {"type": "string", "default": "alpha"}}},
        "detailedInstructions": "version={{params.target_version}} phase={{params.phase}}",
        "oracle": {"verify": {"commands": [{"command": ["echo", "{{params.phase}}"]}]}},
    }
    params, warnings = resolve_case_params(
        case_data,
        {},
        allow_unresolved_top_level_keys={"detailedInstructions"},
    )
    assert params == {"phase": "alpha"}
    assert warnings == []

    rendered = render_case_data_with_params(
        case_data,
        params,
        allow_unresolved_top_level_keys={"detailedInstructions"},
    )
    assert rendered["detailedInstructions"] == "version={{params.target_version}} phase=alpha"
    assert rendered["oracle"]["verify"]["commands"][0]["command"][1] == "alpha"


def test_resolve_case_params_keeps_execution_fields_strict_even_when_prompt_fields_are_relaxed():
    case_data = {
        "params": {"definitions": {"phase": {"type": "string", "default": "alpha"}}},
        "detailedInstructions": "version={{params.target_version}}",
        "oracle": {"verify": {"commands": [{"command": ["echo", "{{params.target_version}}"]}]}},
    }
    try:
        resolve_case_params(
            case_data,
            {},
            allow_unresolved_top_level_keys={"detailedInstructions"},
        )
        assert False, "expected ValueError for unresolved execution token during validation"
    except ValueError as exc:
        assert "undefined params referenced in templates: target_version" in str(exc)


def test_resolve_case_params_warns_for_unused_definition():
    case_data = {
        "params": {
            "definitions": {
                "phase": {"type": "string", "default": "alpha"},
                "unused_param": {"type": "string", "default": "x"},
            }
        },
        "detailedInstructions": "phase={{params.phase}}",
    }
    params, warnings = resolve_case_params(case_data, {})
    assert params["phase"] == "alpha"
    assert any("unused_param" in item for item in warnings)


def test_render_case_data_with_full_token_preserves_types():
    case_data = {
        "params": {"definitions": {"replicas": {"type": "int", "default": 4}}},
        "oracle": {"verify": {"commands": [{"command": ["echo", "{{params.replicas}}"]}]}},
    }
    params, _ = resolve_case_params(case_data, {})
    rendered = render_case_data_with_params(case_data, params)
    token_value = rendered["oracle"]["verify"]["commands"][0]["command"][1]
    assert isinstance(token_value, int)
    assert token_value == 4
