"""Unit tests for the case parameter validation/coercion engine."""

import pytest
from karma.definitions.cases import resolve_case_params


def _case(params):
    return {"prompt": "p", "params": params}


class TestParamCoercion:
    def test_untyped_param_keeps_native_value(self):
        # No type declared -> value is untouched (int stays int).
        resolved, _ = resolve_case_params(_case({"n": {"default": 3}}))
        assert resolved["n"] == 3 and isinstance(resolved["n"], int)

    def test_int_type_coerces_override_string(self):
        resolved, _ = resolve_case_params(
            _case({"n": {"default": 1, "type": "int"}}), {"n": "4"}
        )
        assert resolved["n"] == 4 and isinstance(resolved["n"], int)

    def test_bool_type_coerces(self):
        resolved, _ = resolve_case_params(
            _case({"b": {"default": False, "type": "bool"}}), {"b": "yes"}
        )
        assert resolved["b"] is True

    def test_enum_accepts_member_rejects_nonmember(self):
        case = _case({"m": {"default": "a", "type": "enum", "values": ["a", "b"]}})
        assert resolve_case_params(case, {"m": "b"})[0]["m"] == "b"
        with pytest.raises(ValueError, match="not in"):
            resolve_case_params(case, {"m": "z"})


class TestParamConstraints:
    def test_min_max_enforced(self):
        case = _case({"n": {"default": 3, "type": "int", "min": 1, "max": 5}})
        with pytest.raises(ValueError, match="< min"):
            resolve_case_params(case, {"n": 0})
        with pytest.raises(ValueError, match="> max"):
            resolve_case_params(case, {"n": 9})

    def test_pattern_enforced(self):
        case = _case({"host": {"default": "svc-1", "pattern": r"^svc-\d+$"}})
        assert resolve_case_params(case, {"host": "svc-42"})[0]["host"] == "svc-42"
        with pytest.raises(ValueError, match="does not match pattern"):
            resolve_case_params(case, {"host": "nope"})

    def test_required_missing_raises(self):
        case = _case({"token": {"type": "string", "required": True}})
        with pytest.raises(ValueError, match="required param missing"):
            resolve_case_params(case)

    def test_required_satisfied_by_override(self):
        case = _case({"token": {"type": "string", "required": True}})
        resolved, _ = resolve_case_params(case, {"token": "abc"})
        assert resolved["token"] == "abc"

    def test_unknown_override_warns_not_raises(self):
        resolved, warnings = resolve_case_params(_case({"a": {"default": 1}}), {"b": 2})
        assert resolved["b"] == 2
        assert any("unrecognized param override 'b'" in w for w in warnings)
