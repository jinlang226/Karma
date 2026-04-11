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
    ...


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
    ...


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
    ...
