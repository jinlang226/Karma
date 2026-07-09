"""Unit tests for karma.adversary.definitions path-safety (SR5).

The adversary scenario name flows into a filesystem path
(adversaries/<service>/<scenario>/scenario.yaml) whose deploy commands later
run via shell=True. A traversal name must be rejected before it can escape the
adversaries/ tree.
"""

import pytest

from karma.adversary.definitions import resolve_adversary_scenario, _is_safe_segment


class TestSafeSegment:
    def test_accepts_plain_name(self):
        assert _is_safe_segment("network-partition")

    @pytest.mark.parametrize("bad", ["", ".", "..", "../evil", "a/b", "a\\b"])
    def test_rejects_traversal(self, bad):
        assert not _is_safe_segment(bad)


class TestResolveAdversaryScenarioTraversal:
    def _entry(self, scenario):
        return {"scenario": scenario, "inject_at_stage": "stage_1",
                "lift_at_stage": None, "param_overrides": {}}

    def test_traversal_scenario_name_rejected(self, tmp_path):
        # A "../../../../tmp/evil" name must fail before the path is built --
        # not resolve to a scenario.yaml outside adversaries/ (SR5).
        entry = self._entry("../../../../tmp/evil")
        with pytest.raises(RuntimeError, match="invalid name"):
            resolve_adversary_scenario(
                entry, {"stage_1": "rabbitmq"}, resources_dir=tmp_path / "cases"
            )

    def test_slash_in_scenario_name_rejected(self, tmp_path):
        entry = self._entry("svc/nested")
        with pytest.raises(RuntimeError, match="invalid name"):
            resolve_adversary_scenario(
                entry, {"stage_1": "rabbitmq"}, resources_dir=tmp_path / "cases"
            )
