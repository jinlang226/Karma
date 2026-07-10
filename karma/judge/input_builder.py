"""
Judge request assembly from run artifacts and evidence.

Reads oracle verdict, evidence, kubectl snapshot, and the agent's
submitted answer from the stage run directory, then assembles a
structured input payload consumed by ``judge.client`` and rendered
into the judge prompt.

This module does not call the LLM. It performs only filesystem reads
and data transformation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import protocol
from ..evidence import compute_trace_facts

# Max agent-log bytes embedded in the judge input; longer logs keep the tail
# (where the final answer and outcome are) and drop the head.
_AGENT_LOG_CAP = 24000

# Prefaces the reconstructed stage task so the judge reads unresolved ${...} tokens
# as placeholders, not as the task's literal text.
_STAGE_TASK_CAVEAT = (
    "(The stage's own task, reconstructed from its case definition. Runtime "
    "placeholders such as ${BENCH_NAMESPACE} are shown unresolved -- the concrete "
    "values are in the evidence below.)"
)


def _run_stages(run_dir: Path) -> list[dict[str, Any]]:
    """Ordered stage descriptors from config.json (id/service/case_name/params)."""
    try:
        cfg = json.loads((run_dir / "config.json").read_text())
    except Exception:
        return []
    return cfg.get("stages") or []


def reconstruct_stage_task(
    run_dir: Path, stage_id: str, resources_dir: Path | None = None
) -> str:
    """Return the stage's OWN rendered task -- its case prompt -- independent of the
    workflow's prompt mode.

    The stored prompt.txt is the mode-assembled prompt (for concat modes, the whole
    workflow), so it does not isolate the stage being judged. Reconstruct the single
    task by loading the case named in config.json -- exactly like the UI's
    jump-to-case (load_case_file + normalize_case + render_stage_prompt), using the
    default resources dir. Runtime ${...} placeholders stay unresolved (no live env
    post-hoc). Returns "" when the stage or case cannot be resolved.
    """
    stage = next((s for s in _run_stages(run_dir) if s.get("id") == stage_id), None)
    if not stage:
        return ""
    service, case_name = stage.get("service"), stage.get("case_name")
    if not service or not case_name:
        return ""
    from ..definitions.cases import load_case_file, normalize_case
    from ..definitions.prompts import render_stage_prompt
    from ..settings import settings as _settings
    rd = Path(resources_dir) if resources_dir else _settings.resources_dir
    try:
        data = load_case_file(rd, service, case_name)
        norm = normalize_case(data, service, case_name, stage.get("param_overrides") or {})
        return render_stage_prompt(norm, stage, {"id": run_dir.name}).strip()
    except Exception:
        return ""


def stage_position(run_dir: Path, stage_id: str) -> str:
    """Return a "STAGE k of n" label for *stage_id*, or "" when not resolvable."""
    ids = [s.get("id") for s in _run_stages(run_dir)]
    if stage_id in ids:
        return f"STAGE {ids.index(stage_id) + 1} of {len(ids)}"
    return ""


def build_judge_input(
    run_dir: Path,
    stage_id: str,
    *,
    rubric: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the judge request payload for one stage.

    Raises
    ------
    RuntimeError
        When the oracle or evidence artifact is absent from *run_dir*.

    Returns
    -------
    dict
        Keys: ``stage_id``, ``rubric``, ``oracle``, ``evidence``,
        ``trace_facts``, ``submit_text`` (str or ``None``),
        ``prompt_text`` (str or ``None``), ``stage_task``, ``stage_position``,
        ``agent_log`` (tail-capped str or ``None``). The regression sweep is
        deliberately excluded (see the NOTE below).
    """
    oracle_path = protocol.stage_oracle_path(run_dir, stage_id)
    evidence_path = protocol.stage_evidence_path(run_dir, stage_id)

    if not oracle_path.exists():
        raise RuntimeError(
            f"oracle artifact missing for stage {stage_id}: {oracle_path}"
        )
    if not evidence_path.exists():
        raise RuntimeError(
            f"evidence artifact missing for stage {stage_id}: {evidence_path}"
        )

    try:
        oracle = json.loads(oracle_path.read_text())
    except Exception as exc:
        raise RuntimeError(f"failed to parse oracle artifact: {exc}") from exc

    try:
        evidence = json.loads(evidence_path.read_text())
    except Exception as exc:
        raise RuntimeError(f"failed to parse evidence artifact: {exc}") from exc

    trace_facts = compute_trace_facts(evidence.get("kubectl_snapshot") or [])

    submit_text: str | None = None
    submit_path = protocol.stage_submit_path(run_dir, stage_id)
    if submit_path.exists():
        try:
            submit_text = submit_path.read_text()
        except Exception:
            pass

    prompt_text: str | None = None
    prompt_path = protocol.stage_prompt_path(run_dir, stage_id)
    if prompt_path.exists():
        try:
            prompt_text = prompt_path.read_text()
        except Exception:
            pass

    # Agent log: the full turn-by-turn (reasoning + every tool call) the
    # claude_code entrypoint records -- key evidence of HOW the agent worked.
    agent_log: str | None = None
    agent_log_path = prompt_path.parent / "agent.log"
    if agent_log_path.exists():
        try:
            txt = agent_log_path.read_text()
            agent_log = (txt if len(txt) <= _AGENT_LOG_CAP
                         else "...[truncated head]...\n" + txt[-_AGENT_LOG_CAP:])
        except Exception:
            pass

    # NOTE: the regression sweep is deliberately NOT included here. It grades the
    # stage on its own merits; a sweep failure is a CROSS-stage concern handled
    # separately by the regression adjudication (real regression -> zero the
    # stage; false positive -> no penalty). Feeding the raw sweep to the rubric
    # made it dock a stage for a "brittle pass" that the adjudication then ruled a
    # false positive -- double-penalizing the same failure.
    return {
        "stage_id": stage_id,
        "rubric": rubric,
        "oracle": oracle,
        "evidence": evidence,
        "trace_facts": trace_facts,
        "submit_text": submit_text,
        "prompt_text": prompt_text,
        # The stage's OWN task + position, so the judge knows exactly which stage
        # it is grading regardless of prompt mode (the mode-assembled prompt_text
        # does not isolate the stage -- concat_blind in particular).
        "stage_task": reconstruct_stage_task(run_dir, stage_id),
        "stage_position": stage_position(run_dir, stage_id),
        "agent_log": agent_log,
    }


_DEFAULT_JUDGE_TEMPLATE = (
    "You are evaluating an AI agent on a Kubernetes microservice task.\n\n"
    "## Task Being Judged ({stage_position})\n{task_block}\n\n"
    "## Agent Submission\n{submit_text}\n\n"
    "## Agent Log (turn-by-turn: reasoning + tool calls)\n{agent_log}\n\n"
    "## Oracle Verification\n"
    "Verdict: {oracle_verdict}\n\n"
    "## Kubernetes API Activity\n"
    "Total calls: {total_calls} | Mutations: {mutation_calls} | "
    "Reads: {read_calls}\n"
    "Unique resources: {unique_resources} | "
    "Namespaces: {namespaces_touched}\n\n"
    "## Rubric\n\n"
    "{rubric_items}\n\n"
    "Return a JSON array scoring each rubric item:\n"
    '[{"id": "<id>", "score": <0.0-1.0>, "reasoning": "<explanation>"}]'
)


def render_judge_prompt(
    judge_input: dict[str, Any],
    *,
    template: str | None = None,
) -> str:
    """Return the rendered prompt string for the judge LLM.

    Uses the built-in default template when *template* is ``None``. Derives a
    flat set of string values from *judge_input* (rubric items, oracle verdict,
    trace-fact counts, submit/agent-log text) and substitutes each ``{key}``
    placeholder in the template with them.
    """
    if template is None:
        template = _DEFAULT_JUDGE_TEMPLATE

    rubric = judge_input.get("rubric") or {}
    oracle = judge_input.get("oracle") or {}
    raw_trace = judge_input.get("trace_facts")
    trace_facts: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}

    items_lines: list[str] = []
    for item in rubric.get("items") or []:
        items_lines.append(f"[{item['id']}] weight={item['weight']:.4f}")
        items_lines.append(f"  Description: {item['description']}")
        items_lines.append(f"  Rubric: {item['rubric']}")
        items_lines.append("")

    raw_ns = trace_facts.get("namespaces_touched")
    ns_list: list[str] = raw_ns if isinstance(raw_ns, list) else []
    ns_str = ", ".join(ns_list) if ns_list else "(none)"

    # The oracle verdict is always used authoritatively in scoring, but it can
    # be hidden from the judge PROMPT to reduce outcome bias (the LLM otherwise
    # tends to echo the verdict). Controlled by run_judge's include_outcome.
    if judge_input.get("_include_outcome", True):
        verdict_text = str(oracle.get("verdict") or "unknown")
    else:
        verdict_text = "(hidden to reduce outcome bias)"

    # Prefer the stage's reconstructed OWN task (with the placeholder caveat) so the
    # judge grades the right stage; fall back to the mode-assembled prompt.txt only
    # when reconstruction failed.
    stage_task = str(judge_input.get("stage_task") or "").strip()
    if stage_task:
        task_block = _STAGE_TASK_CAVEAT + "\n" + stage_task
    else:
        task_block = str(judge_input.get("prompt_text") or "(not available)")

    ctx: dict[str, str] = {
        "stage_id": str(judge_input.get("stage_id") or ""),
        "stage_position": str(judge_input.get("stage_position") or "").strip() or "this stage",
        "task_block": task_block,
        "prompt_text": str(judge_input.get("prompt_text") or "(not available)"),
        "submit_text": str(judge_input.get("submit_text") or "(not submitted)"),
        "agent_log": str(judge_input.get("agent_log") or "(not captured)"),
        "oracle_verdict": verdict_text,
        "total_calls": str(trace_facts.get("total_calls") or 0),
        "mutation_calls": str(trace_facts.get("mutation_calls") or 0),
        "read_calls": str(trace_facts.get("read_calls") or 0),
        "unique_resources": str(trace_facts.get("unique_resources") or 0),
        "namespaces_touched": ns_str,
        "rubric_items": "\n".join(items_lines).rstrip(),
    }

    result = template
    for key, value in ctx.items():
        result = result.replace("{" + key + "}", value)
    return result
