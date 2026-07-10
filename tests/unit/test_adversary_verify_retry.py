"""Adversary verify honors verify_retries/verify_interval_sec (SS-11).

Before the fix these fields were parsed onto the unit but never read, so the
verify always ran once -- a fault that took a beat to manifest/clear produced a
spurious deploy/lift failure.
"""
from karma.adversary.runtime import _run_units, deploy


def _unit(cnt_path, retries):
    # A verify that fails on its 1st attempt and passes on its 2nd, driven by a
    # counter file so successive attempts see different state (like a fault that
    # settles a moment after apply). Runs under shell=True, so no wrapper needed.
    check = (f'n=$(cat {cnt_path} 2>/dev/null || echo 0); n=$((n+1)); '
             f'echo $n > {cnt_path}; [ "$n" -ge 2 ]')
    return {
        "id": "u1",
        "probe_commands": [],
        "apply_commands": [],
        "verify_commands": [{"command": check, "timeout_sec": 10}],
        "verify_retries": retries,
        "verify_interval_sec": 0.0,
    }


def test_verify_retries_until_it_passes(tmp_path):
    res = _run_units(
        [_unit(tmp_path / "cnt", retries=2)],
        role_bindings={}, log_path=tmp_path / "adv.log", env_vars={}, result_id_key="ids",
    )
    assert res["ok"] is True and res["ids"] == ["u1"]


def test_single_attempt_fails_when_not_yet_settled(tmp_path):
    # The default (retries=1) is preserved: one shot, so a not-yet-settled fault
    # still fails -- proving the fix is opt-in and behavior-preserving by default.
    res = _run_units(
        [_unit(tmp_path / "cnt", retries=1)],
        role_bindings={}, log_path=tmp_path / "adv.log", env_vars={}, result_id_key="ids",
    )
    assert res["ok"] is False and res["ids"] == []


# --- The ok signal SW-4 keys on: a failed deploy must be distinguishable so the
# --- stage aborts instead of running (and being scored) without the fault. ---

def test_deploy_reports_ok_false_when_apply_fails(tmp_path):
    unit = {"id": "u", "probe_commands": [],
            "apply_commands": [{"command": "false", "timeout_sec": 5}],
            "verify_commands": []}
    res = deploy([unit], role_bindings={}, log_path=tmp_path / "adv.log", env_vars={})
    assert res["ok"] is False


def test_deploy_of_no_units_is_ok(tmp_path):
    # A non-adversary stage: empty units => ok True, so SW-4's guard never fires.
    res = deploy([], role_bindings={}, log_path=tmp_path / "adv.log", env_vars={})
    assert res["ok"] is True
