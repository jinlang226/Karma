"""Adversary verify honors verify_retries/verify_interval_sec (SS-11).

Before the fix these fields were parsed onto the unit but never read, so the
verify always ran once -- a fault that took a beat to manifest/clear produced a
spurious deploy/lift failure.
"""
from karma.adversary.runtime import _run_units


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
