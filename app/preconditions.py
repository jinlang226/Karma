from __future__ import annotations

from typing import Any

from .util import normalize_commands


def _coerce_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return int(parsed)


def _coerce_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    return float(parsed)


def _normalize_step_commands(raw: Any, label: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        if "commands" in raw:
            return normalize_commands(raw.get("commands"))
        if "command" in raw:
            item = {
                "command": raw.get("command"),
                "sleep": raw.get("sleep", 0),
            }
            namespace_role = raw.get("namespace_role")
            if namespace_role is None:
                namespace_role = raw.get("namespaceRole")
            if namespace_role is not None:
                item["namespace_role"] = namespace_role
            if raw.get("timeout_sec") is not None:
                item["timeout_sec"] = raw.get("timeout_sec")
            elif raw.get("timeoutSec") is not None:
                item["timeout_sec"] = raw.get("timeoutSec")
            return normalize_commands([item])
    return normalize_commands(raw)


def normalize_precondition_units(case_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    data = case_data or {}
    raw = data.get("preconditionUnits")
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise ValueError("preconditionUnits must be a list")

    units: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"preconditionUnits[{idx}] must be an object")

        unit_id = str(entry.get("id") or "").strip()
        if not unit_id:
            raise ValueError(f"preconditionUnits[{idx}].id is required")
        if unit_id in seen_ids:
            raise ValueError(f"duplicate precondition unit id: {unit_id}")
        seen_ids.add(unit_id)

        probe_commands = _normalize_step_commands(entry.get("probe"), "probe")
        apply_commands = _normalize_step_commands(entry.get("apply"), "apply")
        verify_commands = _normalize_step_commands(entry.get("verify"), "verify")

        if not probe_commands:
            raise ValueError(f"preconditionUnits[{idx}] ({unit_id}) probe command(s) are required")
        if not apply_commands:
            raise ValueError(f"preconditionUnits[{idx}] ({unit_id}) apply command(s) are required")
        if not verify_commands:
            raise ValueError(f"preconditionUnits[{idx}] ({unit_id}) verify command(s) are required")

        verify_block = entry.get("verify") if isinstance(entry.get("verify"), dict) else {}
        retries = _coerce_int(
            verify_block.get("retries") if isinstance(verify_block, dict) else None,
            default=1,
        )
        interval_sec = _coerce_float(
            (
                verify_block.get("interval_sec")
                if isinstance(verify_block, dict)
                else None
            )
            or (
                verify_block.get("intervalSec")
                if isinstance(verify_block, dict)
                else None
            ),
            default=0,
        )

        units.append(
            {
                "id": unit_id,
                "probe_commands": probe_commands,
                "apply_commands": apply_commands,
                "verify_commands": verify_commands,
                "verify_retries": max(1, retries),
                "verify_interval_sec": max(0.0, interval_sec),
            }
        )

    return units
