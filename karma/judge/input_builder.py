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
        ``prompt_text`` (str or ``None``).
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

    return {
        "stage_id": stage_id,
        "rubric": rubric,
        "oracle": oracle,
        "evidence": evidence,
        "trace_facts": trace_facts,
        "submit_text": submit_text,
        "prompt_text": prompt_text,
    }


def render_judge_prompt(
    judge_input: dict[str, Any],
    *,
    template: str | None = None,
) -> str:
    """Return the rendered prompt string for the judge LLM.

    Uses the built-in default template when *template* is ``None``. The
    template receives the full *judge_input* dict as its rendering context.
    """
    ...
