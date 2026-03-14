from __future__ import annotations

from typing import Any

from .util import normalize_commands


def _normalize_hooks(raw_hooks: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    hooks = raw_hooks if isinstance(raw_hooks, dict) else {}
    before_cmds = normalize_commands(hooks.get("beforeCommands") or hooks.get("before_commands"))
    after_cmds = normalize_commands(hooks.get("afterCommands") or hooks.get("after_commands"))
    mode = str(hooks.get("afterFailureMode") or hooks.get("after_failure_mode") or "warn").strip().lower()
    if mode not in ("warn", "fail"):
        mode = "warn"
    return before_cmds, after_cmds, mode


def resolve_oracle_verify(case_data: dict[str, Any] | None) -> dict[str, Any]:
    data = case_data or {}
    oracle = data.get("oracle") if isinstance(data.get("oracle"), dict) else {}
    verify = oracle.get("verify") if isinstance(oracle.get("verify"), dict) else None

    if isinstance(verify, dict):
        commands = normalize_commands(verify.get("commands"))
        hook_before, hook_after, after_mode = _normalize_hooks(verify.get("hooks"))
    else:
        commands = []
        hook_before, hook_after, after_mode = [], [], "warn"

    return {
        "source": "oracle.verify",
        "commands": commands,
        "before_commands": hook_before,
        "after_commands": hook_after,
        "after_failure_mode": after_mode,
    }
