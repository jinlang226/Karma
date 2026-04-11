"""
Score aggregation and evidence validation for judge results.

Parses the raw LLM response into per-item scores, computes the weighted
aggregate, determines the final verdict, and cross-validates the LLM
score against the oracle result.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Any:
    """Return the first JSON value found in *text*, or ``None`` on failure.

    Tries a fenced code block first, then falls back to parsing the entire
    string as JSON.
    """
    match = _JSON_FENCE_RE.search(text)
    candidate = match.group(1) if match else text.strip()
    try:
        return json.loads(candidate)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_llm_scores(
    raw_response: dict[str, Any],
    *,
    rubric: dict[str, Any],
) -> list[dict[str, Any]]:
    """Parse the LLM response content into per-item score dicts.

    Expects the LLM to return JSON containing a list or dict of scoring
    objects, each with an ``"id"``, a ``"score"`` (float in
    ``[0.0, 1.0]``), and a ``"reasoning"`` string.

    Rubric items not scored by the LLM receive a zero score with
    ``reasoning`` set to ``"not scored by judge"``.

    Parameters
    ----------
    raw_response:
        Raw response dict from ``judge.client.call_judge_llm``.
    rubric:
        Normalized rubric dict from ``judge.rubric.normalize_rubric``.

    Returns
    -------
    list[dict]
        One entry per rubric item with keys ``id``, ``score``, and
        ``reasoning``.
    """
    content = raw_response.get("content") or ""
    parsed = _extract_json(content)

    rubric_items = {item["id"]: item for item in (rubric.get("items") or [])}
    scores: dict[str, dict[str, Any]] = {}

    if isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id") or "").strip()
            if not item_id:
                continue
            try:
                score = float(entry.get("score", 0.0))
            except Exception:
                score = 0.0
            scores[item_id] = {
                "id": item_id,
                "score": max(0.0, min(1.0, score)),
                "reasoning": str(entry.get("reasoning") or "").strip(),
            }
    elif isinstance(parsed, dict):
        for item_id, value in parsed.items():
            item_id = str(item_id).strip()
            if isinstance(value, dict):
                try:
                    score = float(value.get("score", 0.0))
                except Exception:
                    score = 0.0
                reasoning = str(value.get("reasoning") or "").strip()
            else:
                try:
                    score = float(value)
                except Exception:
                    score = 0.0
                reasoning = ""
            scores[item_id] = {
                "id": item_id,
                "score": max(0.0, min(1.0, score)),
                "reasoning": reasoning,
            }

    return [
        scores.get(item_id) or {
            "id": item_id,
            "score": 0.0,
            "reasoning": "not scored by judge",
        }
        for item_id in rubric_items
    ]


def compute_aggregate_score(
    item_scores: list[dict[str, Any]],
    *,
    rubric: dict[str, Any],
) -> float:
    """Return the weighted aggregate score from *item_scores*.

    Weights are taken from the rubric item definitions. Items absent from
    the rubric are excluded. Returns a float in ``[0.0, 1.0]`` rounded to
    four decimal places.
    """
    rubric_weights = {
        item["id"]: float(item.get("weight", 0.0))
        for item in (rubric.get("items") or [])
    }
    total_weight = sum(rubric_weights.values())
    if total_weight <= 0.0:
        return 0.0
    weighted_sum = sum(
        s["score"] * rubric_weights.get(s["id"], 0.0)
        for s in item_scores
    )
    return round(weighted_sum / total_weight, 4)


def determine_verdict(
    aggregate_score: float,
    *,
    rubric: dict[str, Any],
    oracle_verdict: str | None = None,
) -> str:
    """Return the final verdict string for a stage evaluation.

    When *oracle_verdict* is ``"fail"`` the verdict is always ``"fail"``
    regardless of the LLM score, because the oracle is the authoritative
    correctness check.

    Otherwise the verdict is determined by comparing *aggregate_score*
    against the rubric ``passing_threshold``:

    - ``score >= threshold``      → ``"pass"``
    - ``score >= threshold / 2``  → ``"partial"``
    - ``score < threshold / 2``   → ``"fail"``
    """
    if oracle_verdict == "fail":
        return "fail"
    threshold = float(rubric.get("passing_threshold", 0.7))
    if aggregate_score >= threshold:
        return "pass"
    if aggregate_score >= threshold / 2:
        return "partial"
    return "fail"


def aggregate_scores(
    raw_response: dict[str, Any],
    *,
    rubric: dict[str, Any],
    stage_id: str,
    oracle_verdict: str | None = None,
) -> dict[str, Any]:
    """Parse, aggregate, and validate LLM scores into a final judge result.

    Combines :func:`parse_llm_scores`, :func:`compute_aggregate_score`,
    and :func:`determine_verdict` into a single call.

    Parameters
    ----------
    raw_response:
        Raw response dict from ``judge.client.call_judge_llm``.
    rubric:
        Normalized rubric dict.
    stage_id:
        Stage ID included in the returned dict for traceability.
    oracle_verdict:
        Oracle verdict string used to enforce fail-on-oracle-fail logic.

    Returns
    -------
    dict
        Keys: ``stage_id``, ``verdict``, ``score``, ``rubric_items``,
        ``reasoning``, ``raw_response``.
    """
    item_scores = parse_llm_scores(raw_response, rubric=rubric)
    score = compute_aggregate_score(item_scores, rubric=rubric)
    verdict = determine_verdict(
        score, rubric=rubric, oracle_verdict=oracle_verdict
    )
    reasoning = "\n".join(
        f"{s['id']}: {s['reasoning']}"
        for s in item_scores
        if s.get("reasoning")
    )
    return {
        "stage_id": stage_id,
        "verdict": verdict,
        "score": score,
        "rubric_items": item_scores,
        "reasoning": reasoning,
        "raw_response": raw_response,
    }
