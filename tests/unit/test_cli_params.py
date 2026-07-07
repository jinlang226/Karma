"""Unit tests for --param parsing (karma.interfaces.cli.main, M7).

--param JSON-decodes so typed values (ints, bools, lists) reach cases, but that
silently mangled version tags: "1.10" -> float 1.1 -> "1.1". Numeric-looking
strings must survive, via an automatic round-trip guard and an explicit `str:`
escape.
"""

from karma.interfaces.cli.main import _parse_param_overrides


class TestParseParamOverrides:
    def test_typed_values_are_preserved(self):
        r = _parse_param_overrides(["replicas=3", "enabled=true", "nodes=[\"a\",\"b\"]"])
        assert r["replicas"] == 3 and isinstance(r["replicas"], int)
        assert r["enabled"] is True
        assert r["nodes"] == ["a", "b"]

    def test_version_string_is_not_mangled(self):
        # The confirmed bug: "1.10" must stay the string "1.10", not become 1.1.
        r = _parse_param_overrides(["version=1.10"])
        assert r["version"] == "1.10" and isinstance(r["version"], str)

    def test_str_escape_forces_string(self):
        r = _parse_param_overrides(["version=str:1.0", "flag=str:true"])
        assert r["version"] == "1.0" and isinstance(r["version"], str)
        assert r["flag"] == "true" and isinstance(r["flag"], str)

    def test_round_tripping_floats_stay_numeric(self):
        r = _parse_param_overrides(["ratio=1.5"])
        assert r["ratio"] == 1.5 and isinstance(r["ratio"], float)

    def test_plain_string_stays_string(self):
        assert _parse_param_overrides(["name=foo"])["name"] == "foo"
