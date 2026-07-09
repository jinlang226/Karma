"""
Prompt rendering and placeholder expansion.

Prompt modes:

``progressive``
    Each stage receives only its own task prompt. Suitable when the
    agent maintains its own conversation context across stages.

``concat_stateful``
    The agent receives EVERY stage's prompt -- past, current, and future --
    concatenated, each headed with its 1-based position and a status
    (``COMPLETED`` / ``ACTIVE`` / ``UPCOMING``) relative to the stage now
    running, so it sees the full workflow plan and which stage to work on now.

``concat_blind``
    The same full-workflow concatenation as ``concat_stateful`` but with no
    headers, positions, or status markers -- the agent sees every stage's task
    yet is deliberately blind to where it is in the sequence (it infers
    progress from submit/state files).

Both concat modes expose the whole workflow, future stages included, so the
prompt is built from the workflow definition (all stages known up front), not
from an accumulated runtime history.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .._warn import warn

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.:-]+)\}")

VALID_PROMPT_MODES = ("progressive", "concat_stateful", "concat_blind")

# Per-mode prologue orientation prepended to the assembled prompt. Single source
# of truth: docs/prompt-mode-prologues.yaml (overridable via --prompt-mode-prologues).
_PROLOGUE_PATH = Path(__file__).resolve().parents[2] / "docs" / "prompt-mode-prologues.yaml"
_REQUIRED_PROLOGUE_KEYS = frozenset(VALID_PROMPT_MODES)


def load_prompt_mode_prologues(path: str | None = None) -> dict[str, str]:
    """Return the per-mode prologue texts, keyed by prompt mode.

    Reads *path* when given (the ``--prompt-mode-prologues`` override), else the
    default ``docs/prompt-mode-prologues.yaml``. STRICT validation: the file must
    be a mapping with EXACTLY the three mode keys (``progressive``,
    ``concat_stateful``, ``concat_blind``), each a non-empty string -- a missing
    OR unknown key raises, so a typo cannot leave a mode silently prologue-less.

    A missing/unreadable DEFAULT warns loudly and yields empty prologues (mirrors
    the other prompt loaders); a bad EXPLICIT ``path`` raises (the user asked for
    it, so surface the error).
    """
    p = Path(path) if path else _PROLOGUE_PATH
    try:
        raw = yaml.safe_load(p.read_text())
    except Exception as exc:
        if path:
            raise RuntimeError(f"prompt-mode prologues not readable: {p}: {exc}") from exc
        warn(f"default prompt-mode prologues not found at {p}: {exc}")
        return {k: "" for k in _REQUIRED_PROLOGUE_KEYS}
    if not isinstance(raw, dict):
        raise RuntimeError(f"prompt-mode prologues {p}: must be a YAML mapping")
    keys = set(raw)
    if keys != _REQUIRED_PROLOGUE_KEYS:
        missing = sorted(_REQUIRED_PROLOGUE_KEYS - keys)
        unknown = sorted(keys - _REQUIRED_PROLOGUE_KEYS)
        raise RuntimeError(
            f"prompt-mode prologues {p}: must define EXACTLY "
            f"{sorted(_REQUIRED_PROLOGUE_KEYS)}"
            + (f"; missing {missing}" if missing else "")
            + (f"; unknown {unknown}" if unknown else "")
        )
    out: dict[str, str] = {}
    for k in _REQUIRED_PROLOGUE_KEYS:
        v = raw[k]
        if not isinstance(v, str) or not v.strip():
            raise RuntimeError(f"prompt-mode prologues {p}: '{k}' must be a non-empty string")
        out[k] = v.strip()
    return out


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

    Applies *prompt_mode* to decide which stages to include and how to mark
    them, then appends *adversary_hint* when provided. The concat modes span
    the whole *stage_prompts* list (future stages included); ``progressive``
    returns only the current stage.

    Parameters
    ----------
    stage_prompts:
        Rendered prompt strings for ALL stages in the workflow (not just those
        up to the current one), indexed by stage position.
    current_index:
        Zero-based index of the currently-running stage within *stage_prompts*.
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
    total = len(stage_prompts)

    if prompt_mode == "progressive":
        assembled = current_prompt
    elif prompt_mode == "concat_stateful":
        # The FULL workflow -- future stages included -- each headed with its
        # 1-based position and a status relative to the active stage, so the
        # agent sees the whole plan and knows which stage to work on now.
        parts: list[str] = []
        for i, p in enumerate(stage_prompts):
            if i < current_index:
                status = "COMPLETED"
            elif i == current_index:
                status = "ACTIVE (work on this stage now)"
            else:
                status = "UPCOMING"
            parts.append(f"=== STAGE {i + 1} of {total} -- {status} ===\n{p}")
        assembled = "\n\n".join(parts)
    else:  # concat_blind
        # Full workflow, but no headers/position/status -- the agent is blind to
        # where it is in the sequence (progress comes from submit/state files).
        assembled = "\n\n".join(stage_prompts)

    if adversary_hint:
        assembled = assembled + "\n\n" + adversary_hint.strip()

    return assembled
