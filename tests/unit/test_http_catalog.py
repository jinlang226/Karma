"""Unit tests for karma.interfaces.http.catalog."""

import json

from karma.interfaces.http import catalog


def _write_case(resources_dir, service, case, body):
    p = resources_dir / service / case / "test.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


class TestListServices:
    def test_lists_services_with_case_counts(self, tmp_path):
        _write_case(tmp_path, "svc-a", "case-1", "prompt: hi\n")
        _write_case(tmp_path, "svc-a", "case-2", "prompt: hi\n")
        _write_case(tmp_path, "svc-b", "case-1", "prompt: hi\n")
        services = catalog.list_services(tmp_path)
        names = {s["name"]: s for s in services}
        assert names["svc-a"]["case_count"] == 2
        assert names["svc-a"]["cases"] == ["case-1", "case-2"]
        assert names["svc-b"]["case_count"] == 1

    def test_skips_dirs_without_test_yaml(self, tmp_path):
        (tmp_path / "svc-a" / "not-a-case").mkdir(parents=True)
        _write_case(tmp_path, "svc-a", "real", "prompt: hi\n")
        services = catalog.list_services(tmp_path)
        assert services[0]["cases"] == ["real"]

    def test_missing_resources_dir_returns_empty(self, tmp_path):
        assert catalog.list_services(tmp_path / "nope") == []


class TestCasesByService:
    def test_maps_service_to_cases(self, tmp_path):
        _write_case(tmp_path, "svc", "c1", "prompt: hi\n")
        assert catalog.list_cases_by_service(tmp_path) == {"svc": ["c1"]}


class TestGetCaseDetail:
    def test_returns_prompt_and_params(self, tmp_path):
        _write_case(
            tmp_path,
            "svc",
            "c1",
            "prompt: do the thing\n"
            "params:\n"
            "  target:\n"
            "    default: 5\n"
            "    description: target value\n"
            "metrics:\n  - blast_radius\n"
            "tags:\n  - smoke\n",
        )
        detail = catalog.get_case_detail(tmp_path, "svc", "c1")
        assert detail["prompt"] == "do the thing"
        assert detail["params"] == [
            {"name": "target", "default": 5, "description": "target value"}
        ]
        assert detail["metrics"] == ["blast_radius"]
        assert detail["tags"] == ["smoke"]

    def test_raises_for_missing_case(self, tmp_path):
        import pytest

        with pytest.raises(RuntimeError):
            catalog.get_case_detail(tmp_path, "svc", "nope")

    def test_rejects_path_traversal_in_names(self, tmp_path):
        import pytest

        # A traversal segment must be rejected, not used to read outside
        # resources_dir.
        for service, case in [("..", "x"), ("svc", ".."), ("svc", "../../etc"),
                              ("a/b", "c")]:
            with pytest.raises(RuntimeError, match="invalid"):
                catalog.get_case_detail(tmp_path, service, case)


class TestListRuns:
    def test_lists_runs_newest_first_with_scores(self, tmp_path):
        runs = tmp_path / "runs"
        for rid in ("run-a", "run-b"):
            rd = runs / rid
            (rd).mkdir(parents=True)
            (rd / "workflow_state.json").write_text(json.dumps({"status": "complete"}))
            sd = rd / "stages" / "stage_1"
            sd.mkdir(parents=True)
            (sd / "judge.json").write_text(json.dumps({"score": 0.8}))
        result = catalog.list_runs(runs)
        # newest first => reverse-sorted name => run-b before run-a
        assert [r["run_id"] for r in result] == ["run-b", "run-a"]
        assert result[0]["status"] == "complete"
        assert result[0]["judged"] is True
        assert result[0]["judge_score"] == 0.8
        assert result[0]["stage_count"] == 1

    def test_unjudged_run_has_no_score(self, tmp_path):
        runs = tmp_path / "runs"
        rd = runs / "r1"
        (rd / "stages" / "s1").mkdir(parents=True)
        result = catalog.list_runs(runs)
        assert result[0]["judged"] is False
        assert "judge_score" not in result[0]

    def test_missing_runs_dir_returns_empty(self, tmp_path):
        assert catalog.list_runs(tmp_path / "nope") == []


class TestListWorkflowFiles:
    def test_valid_workflow_parsed(self, tmp_path):
        _write_case(tmp_path / "resources", "svc", "c1", "prompt: hi\n")
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "demo.yaml").write_text(
            "metadata:\n  id: demo-flow\n"
            "spec:\n  stages:\n    - id: s1\n      service: svc\n      case: c1\n"
        )
        result = catalog.list_workflow_files(wf_dir, tmp_path / "resources")
        assert result[0]["ok"] is True
        assert result[0]["id"] == "demo-flow"
        assert result[0]["stage_count"] == 1

    def test_invalid_workflow_flagged(self, tmp_path):
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        (wf_dir / "bad.yaml").write_text("metadata: {}\nspec: {}\n")
        result = catalog.list_workflow_files(wf_dir, tmp_path / "resources")
        assert result[0]["ok"] is False
        assert result[0]["errors"]


class TestListAdversaryScenarios:
    def test_discovers_scenarios_with_lift_flag(self, tmp_path):
        scen = tmp_path / "demo" / "adversarial" / "kill-pod" / "scenario.yaml"
        scen.parent.mkdir(parents=True)
        scen.write_text(
            "deploy:\n  probe: kubectl get x\n  apply: kubectl delete x\n  verify: kubectl get x\n"
            "lift:\n  probe: p\n  apply: a\n  verify: v\n"
            "prompt_hints:\n  deploy: a pod was killed\n"
        )
        result = catalog.list_adversary_scenarios(tmp_path)
        assert len(result) == 1
        assert result[0]["service"] == "demo"
        assert result[0]["scenario"] == "kill-pod"
        assert result[0]["has_lift"] is True
        assert result[0]["prompt_hints"]["deploy"] == "a pod was killed"

    def test_no_adversarial_dir_returns_empty(self, tmp_path):
        (tmp_path / "demo" / "some-case").mkdir(parents=True)
        assert catalog.list_adversary_scenarios(tmp_path) == []


class TestSaveWorkflow:
    def _resources(self, tmp_path):
        _write_case(tmp_path / "resources", "svc", "c1", "prompt: hi\n")
        return tmp_path / "resources"

    def _yaml(self):
        return (
            "metadata:\n  id: my flow!\n"
            "spec:\n  stages:\n    - id: s1\n      service: svc\n      case: c1\n"
        )

    def test_saves_to_ui_subfolder_with_sanitized_name(self, tmp_path):
        res = self._resources(tmp_path)
        wf_dir = tmp_path / "workflows"
        out = catalog.save_workflow(wf_dir, res, self._yaml(), "my flow!")
        assert out["ok"] is True
        # "my flow!" sanitized to a safe single segment under ui/
        assert out["name"] == "ui/my-flow.yaml"
        assert (wf_dir / "ui" / "my-flow.yaml").exists()

    def test_appears_in_listing(self, tmp_path):
        res = self._resources(tmp_path)
        wf_dir = tmp_path / "workflows"
        catalog.save_workflow(wf_dir, res, self._yaml(), "saved")
        names = [e["name"] for e in catalog.list_workflow_files(wf_dir, res)]
        assert "ui/saved.yaml" in names

    def test_invalid_workflow_rejected(self, tmp_path):
        import pytest
        res = self._resources(tmp_path)
        with pytest.raises(ValueError):
            catalog.save_workflow(tmp_path / "workflows", res, "metadata: {}\nspec: {}\n", "bad")

    def test_overwrites_same_name(self, tmp_path):
        res = self._resources(tmp_path)
        wf_dir = tmp_path / "workflows"
        catalog.save_workflow(wf_dir, res, self._yaml(), "dup")
        catalog.save_workflow(wf_dir, res, self._yaml(), "dup")
        assert len(list((wf_dir / "ui").glob("*.yaml"))) == 1
