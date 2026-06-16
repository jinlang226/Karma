"""
Prompt rendering and placeholder expansion.

Prompt modes:

``progressive``
    Each stage receives only its own task prompt. Suitable when the
    agent maintains its own conversation context across stages.

``concat_stateful``
    Each stage receives all prior stage prompts prepended, with an
    ``(ACTIVE)`` marker on the current stage.

``concat_blind``
    Same as ``concat_stateful`` but without stage boundary markers.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.:-]+)\}")

VALID_PROMPT_MODES = ("progressive", "concat_stateful", "concat_blind")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _expand_placeholders(template: str, variables: dict[str, str]) -> str:
    """Return *template* with ``${key}`` tokens replaced by *variables* values.

    Unknown tokens are left unchanged.
    """
    def replacer(match: re.Match) -> str:
        return variables.get(match.group(1), match.group(0))
    return _PLACEHOLDER_RE.sub(replacer, template)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_stage_prompt(
    case_data: dict[str, Any],
    stage: dict[str, Any],
    workflow: dict[str, Any],
    *,
    variables: dict[str, str] | None = None,
) -> str:
    """Return the rendered prompt string for one stage.

    Reads the prompt template from ``case_data["prompt"]`` and expands all
    ``${placeholder}`` tokens. Built-in variables include ``stage_id``,
    ``workflow_id``, ``service``, and ``case_name``. Caller-supplied
    *variables* override built-ins.

    Raises
    ------
    ValueError
        When *case_data* contains no usable prompt template.
    """
    template = str(case_data.get("prompt") or "").strip()
    if not template:
        raise ValueError("case has no prompt template")

    builtins: dict[str, str] = {
        "stage_id": str(stage.get("id") or stage.get("stage_id") or ""),
        "workflow_id": str(workflow.get("id") or ""),
        "service": str(stage.get("service") or ""),
        "case_name": str(stage.get("case_name") or ""),
    }
    merged = {**builtins, **(variables or {})}
    return _expand_placeholders(template, merged).rstrip()


def assemble_agent_prompt(
    stage_prompts: list[str],
    current_index: int,
    prompt_mode: str,
    *,
    adversary_hint: str | None = None,
) -> str:
    """Return the final prompt string to deliver to the agent for one stage.

    Applies *prompt_mode* to determine how many prior stage prompts to
    include, then appends *adversary_hint* when provided.

    Parameters
    ----------
    stage_prompts:
        Rendered prompt strings for all stages up to and including the
        current one.
    current_index:
        Zero-based index of the current stage within *stage_prompts*.
    prompt_mode:
        One of ``VALID_PROMPT_MODES``.
    adversary_hint:
        Optional adversary context string appended to the current stage
        prompt.

    Raises
    ------
    ValueError
        When *prompt_mode* is not one of ``VALID_PROMPT_MODES``.
    """
    if prompt_mode not in VALID_PROMPT_MODES:
        raise ValueError(
            f"invalid prompt_mode {prompt_mode!r}; "
            f"expected one of {VALID_PROMPT_MODES}"
        )

    current_prompt = stage_prompts[current_index]

    if prompt_mode == "progressive":
        assembled = current_prompt
    elif prompt_mode == "concat_stateful":
        parts: list[str] = []
        for i, p in enumerate(stage_prompts[: current_index + 1]):
            marker = "(ACTIVE) " if i == current_index else f"(STAGE {i + 1}) "
            parts.append(marker + p)
        assembled = "\n\n".join(parts)
    else:  # concat_blind
        assembled = "\n\n".join(stage_prompts[: current_index + 1])

    if adversary_hint:
        assembled = assembled + "\n\n" + adversary_hint.strip()

    return assembled


def render_workflow_system_prompt(
    workflow: dict[str, Any],
    *,
    variables: dict[str, str] | None = None,
) -> str | None:
    """Return the rendered workflow-level system prompt, or ``None``.

    Returns ``None`` when the workflow declares no system prompt. Used by
    ``runtime.workflow`` to prepend a shared instruction block to each
    stage prompt in multi-stage runs.
    """
    template = str((workflow.get("spec") or {}).get("system_prompt") or "").strip()
    if not template:
        return None
    builtins: dict[str, str] = {"workflow_id": str(workflow.get("id") or "")}
    merged = {**builtins, **(variables or {})}
    return _expand_placeholders(template, merged)
