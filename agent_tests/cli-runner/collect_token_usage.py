#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _safe_int(value):
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def _normalize_usage(value):
    if not isinstance(value, dict):
        return None
    input_tokens = _safe_int(value.get("input_tokens", value.get("inputTokens", 0)))
    cached_input_tokens = _safe_int(
        value.get(
            "cached_input_tokens",
            value.get("cache_read_input_tokens", value.get("cachedInputTokens", 0)),
        )
    )
    output_tokens = _safe_int(value.get("output_tokens", value.get("outputTokens", 0)))
    reasoning_output_tokens = _safe_int(
        value.get("reasoning_output_tokens", value.get("reasoningOutputTokens", 0))
    )
    total_tokens = _safe_int(value.get("total_tokens", value.get("totalTokens", 0)))
    if total_tokens <= 0:
        total_tokens = max(0, input_tokens + output_tokens)

    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
    }


def _subtract_usage(current, previous):
    previous = previous or {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    return {
        "input_tokens": max(0, current["input_tokens"] - previous["input_tokens"]),
        "cached_input_tokens": max(0, current["cached_input_tokens"] - previous["cached_input_tokens"]),
        "output_tokens": max(0, current["output_tokens"] - previous["output_tokens"]),
        "reasoning_output_tokens": max(
            0, current["reasoning_output_tokens"] - previous["reasoning_output_tokens"]
        ),
        "total_tokens": max(0, current["total_tokens"] - previous["total_tokens"]),
    }


def _extract_model(*candidates):
    keys = (
        "model",
        "model_name",
        "model_slug",
        "modelId",
        "model_id",
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in keys:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        info = candidate.get("info")
        if isinstance(info, dict):
            nested = _extract_model(info)
            if nested:
                return nested
        context = candidate.get("context")
        if isinstance(context, dict):
            nested = _extract_model(context)
            if nested:
                return nested
    return None


def _iter_session_files(codex_home):
    sessions_root = codex_home / "sessions"
    if not sessions_root.exists():
        return []
    return sorted(sessions_root.rglob("*.jsonl"))


def collect_codex_usage(codex_home):
    warnings = []
    files = _iter_session_files(codex_home)
    if not files:
        warnings.append(f"no session files under {codex_home / 'sessions'}")

    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 0,
    }
    model_totals = defaultdict(
        lambda: {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
            "count": 0,
        }
    )
    events_count = 0
    token_count_events = 0
    token_count_with_info = 0

    for file_path in files:
        previous_totals = None
        current_model = None
        try:
            with file_path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    entry_type = entry.get("type")
                    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}

                    if entry_type == "turn_context":
                        model = _extract_model(payload, entry)
                        if model:
                            current_model = model
                        continue

                    if entry_type != "event_msg":
                        continue
                    if payload.get("type") != "token_count":
                        continue
                    token_count_events += 1

                    info = payload.get("info") if isinstance(payload.get("info"), dict) else None
                    if info:
                        token_count_with_info += 1

                    last_usage = _normalize_usage((info or {}).get("last_token_usage"))
                    total_usage = _normalize_usage((info or {}).get("total_token_usage"))

                    raw_delta = last_usage
                    if raw_delta is None and total_usage is not None:
                        raw_delta = _subtract_usage(total_usage, previous_totals)
                    if total_usage is not None:
                        previous_totals = total_usage
                    if raw_delta is None:
                        continue
                    if (
                        raw_delta["input_tokens"] == 0
                        and raw_delta["cached_input_tokens"] == 0
                        and raw_delta["output_tokens"] == 0
                        and raw_delta["reasoning_output_tokens"] == 0
                    ):
                        continue

                    model = _extract_model(payload, info, entry) or current_model or "unknown"
                    current_model = model
                    events_count += 1

                    for key in totals:
                        totals[key] += raw_delta[key]
                        model_totals[model][key] += raw_delta[key]
                    model_totals[model]["count"] += 1
        except Exception as exc:
            warnings.append(f"failed to parse {file_path}: {exc}")

    if token_count_events > 0 and token_count_with_info == 0:
        warnings.append("token_count events found without usage info; token extraction may be incomplete")

    return {
        "schema_version": "agent_usage_raw.codex.v1",
        "provider": "codex",
        "source": "codex_session_store",
        "available": events_count > 0,
        "totals": totals,
        "model_breakdown": dict(model_totals),
        "files_scanned": len(files),
        "events_count": events_count,
        "token_count_events": token_count_events,
        "token_count_events_with_info": token_count_with_info,
        "warnings": warnings,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "codex_home": str(codex_home),
    }


def main():
    parser = argparse.ArgumentParser(description="Collect Codex token usage from local session files.")
    parser.add_argument("--out", required=True, help="Output JSON path.")
    parser.add_argument("--codex-home", default=str(Path.home() / ".codex"))
    parser.add_argument("--agent-log", default="", help="Unused, reserved for future parsers.")
    args = parser.parse_args()

    codex_home = Path(args.codex_home).expanduser()
    payload = collect_codex_usage(codex_home)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

