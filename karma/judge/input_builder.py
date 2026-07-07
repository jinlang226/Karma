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
        ``prompt_text`` (str or ``None``), ``agent_log`` (tail-capped str or
        ``None``), ``regression_sweep`` (post-workflow oracle re-run or ``None``).
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
        "agent_log": agent_log,
    }


_DEFAULT_JUDGE_TEMPLATE = (
    "You are evaluating an AI agent on a Kubernetes microservice task.\n\n"
    "## Task Prompt\n{prompt_text}\n\n"
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
    trace-fact counts, submit/agent-log/regression-sweep text) and substitutes
    each ``{key}`` placeholder in the template with them.
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

    ctx: dict[str, str] = {
        "stage_id": str(judge_input.get("stage_id") or ""),
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
