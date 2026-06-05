"""Unit tests for the `manual` CLI subcommand."""

from unittest.mock import patch
from karma.interfaces.cli.main import main


def test_manual_subcommand_drives_lifecycle(capsys):
    # The manual subcommand starts a run, waits for ready, verifies on the
    # operator's signal, and always cleans up -- the terminal equivalent of
    # the HTTP /api/manual/* flow.
    ready = {
        "status": "ready",
        "namespace_bindings": {"default": "karma-r-default"},
        "kubeconfig_path": "/runs/r/bundle/kubeconfig",
        "prompt_path": "/runs/r/stages/manual/prompt.txt",
    }
    with patch("karma.runtime.manual.start_manual_run", return_value="rid1") as m_start, \
         patch("karma.runtime.manual.get_manual_status", return_value=ready), \
         patch("karma.runtime.manual.submit_manual_run",
               return_value={"status": "passed", "oracle_verdict": "pass", "attempts": 1}) as m_submit, \
         patch("karma.runtime.manual.cleanup_manual_run", return_value={}) as m_cleanup, \
         patch("builtins.input", return_value=""):
        main(["manual", "demo", "configmap-update", "--param", "target_value=x"])

    m_start.assert_called_once()
    # operator's params reach start_manual_run
    assert m_start.call_args.kwargs["param_overrides"] == {"target_value": "x"}
    m_submit.assert_called_once_with("rid1")
    m_cleanup.assert_called_once_with("rid1")
    out = capsys.readouterr().out
    assert "karma-r-default" in out
    assert "passed" in out


def test_manual_subcommand_exits_on_setup_failure(capsys):
    with patch("karma.runtime.manual.start_manual_run", return_value="rid2"), \
         patch("karma.runtime.manual.get_manual_status",
               return_value={"status": "setup_failed", "error": "boom"}), \
         patch("karma.runtime.manual.cleanup_manual_run", return_value={}) as m_cleanup, \
         patch("builtins.input", return_value="q"):
        try:
            main(["manual", "demo", "configmap-update"])
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("expected SystemExit(1) on setup failure")
    m_cleanup.assert_called_once_with("rid2")
