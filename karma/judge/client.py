"""
OpenAI-compatible LLM client for judge evaluation calls.

This is the only module in KARMA that calls an external LLM API.
It renders the judge prompt via ``judge.input_builder.render_judge_prompt``,
submits it to either an OpenAI-compatible chat completions endpoint or the
``claude`` CLI (auto-selected by credential availability), and returns the raw
response dict for ``judge.scoring`` to parse.

Configuration is read from environment variables when not supplied
explicitly:

``OPENAI_API_KEY`` / ``KARMA_JUDGE_API_KEY``
    API key for the LLM provider.
``OPENAI_BASE_URL`` / ``KARMA_JUDGE_BASE_URL``
    Base URL for OpenAI-compatible endpoints.
``KARMA_JUDGE_MODEL``
    Default model name override.
``KARMA_JUDGE_BACKEND``
    Force the backend (``openai`` or ``claude_cli``); auto-detected otherwise.

These are read at call time (not via the ``settings`` singleton) because the
backend and credentials are resolved per call and tests override them at runtime.
"""

from __future__ import annotations

import os
import time
from typing import Any

_DEFAULT_MODEL = "gpt-4o"
_DEFAULT_CLAUDE_MODEL = "sonnet"
_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_TEMPERATURE = 0.0
_DEFAULT_TIMEOUT_SEC = 120
_DEFAULT_MAX_RETRIES = 3
_RETRY_BASE_DELAY_SEC = 2.0


def _resolve_backend(backend: str | None, api_key: str | None) -> str:
    """Pick the judge backend: 'openai' or 'claude_cli'.

    Explicit arg wins, then ``KARMA_JUDGE_BACKEND``; otherwise auto -- use the
    ``claude`` CLI (ambient Claude auth, like the agent) when no
    OpenAI-compatible key is available, so judging works without an API key.
    """
    chosen = (backend or os.environ.get("KARMA_JUDGE_BACKEND") or "").strip().lower()
    if chosen in ("openai", "claude_cli"):
        return chosen
    has_key = (
        api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("KARMA_JUDGE_API_KEY")
    )
    return "openai" if has_key else "claude_cli"


def _call_claude_cli(
    prompt: str, model: str, timeout_sec: int
) -> dict[str, Any]:
    """Run the judge prompt through the ``claude`` CLI and return a response.

    Uses ``claude --print`` (ambient Claude login, no API key). The judge only
    needs text generation, so it runs with the executing tools disallowed and
    **without** ``--dangerously-skip-permissions``. That flag would auto-approve
    every tool call, and the judge prompt embeds agent-authored artifacts
    (submit.txt, agent.log, oracle output) -- so a benchmarked agent could inject
    a Bash tool call the judge runs with the operator's host credentials
    (arbitrary command execution). Without skip-permissions, any tool that needs
    approval is denied in non-interactive ``--print`` mode; disallowing the
    executing tools removes them outright. The prompt goes on stdin because
    ``--disallowedTools`` is variadic and would otherwise swallow a trailing
    positional prompt as a tool name.
    Returns the same shape as the OpenAI path so scoring is backend-agnostic.
    """
    import subprocess

    proc = subprocess.run(
        ["claude", "--print", "--model", model,
         "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task"],
        input=prompt,
        capture_output=True, text=True, timeout=timeout_sec,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI judge failed (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    content = (proc.stdout or "").strip()
    if not content:
        raise RuntimeError("claude CLI judge returned empty output")
    return {"content": content, "model": model, "usage": {}, "finish_reason": "stop"}


def _build_client(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
) -> Any:
    """Return an initialized OpenAI-compatible client.

    Raises
    ------
    RuntimeError
        When no API key can be resolved from arguments or environment.
    """
    import openai

    key = (
        api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("KARMA_JUDGE_API_KEY")
    )
    if not key:
        raise RuntimeError(
            "no API key found for judge LLM. "
            "Set OPENAI_API_KEY or KARMA_JUDGE_API_KEY in the environment."
        )
    url = (
        base_url
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("KARMA_JUDGE_BASE_URL")
    )
    kwargs: dict[str, Any] = {"api_key": key, "timeout": timeout_sec}
    if url:
        kwargs["base_url"] = url
    return openai.OpenAI(**kwargs)


def call_judge_llm(
    judge_input: dict[str, Any] | None,
    *,
    prompt: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    backend: str | None = None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = _DEFAULT_TEMPERATURE,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Submit the judge input to the LLM and return the raw response dict.

    Sends *prompt* (or, when not given, the prompt rendered from *judge_input*
    via ``judge.input_builder.render_judge_prompt``) to the resolved backend:
    the OpenAI-compatible chat completions endpoint, or the ``claude`` CLI when
    no API key is available. Retries on transient errors (rate limits, timeouts,
    5xx responses) with exponential backoff.

    Parameters
    ----------
    judge_input:
        Assembled judge input from ``judge.input_builder.build_judge_input``.
    prompt:
        Fully-rendered prompt to send as-is; when ``None`` it is rendered from
        *judge_input*.
    model:
        Model name override. Falls back to ``KARMA_JUDGE_MODEL`` then the
        backend default (``"gpt-4o"`` for openai, ``"sonnet"`` for claude_cli).
    base_url:
        Base URL override for OpenAI-compatible endpoints.
    api_key:
        API key override.
    backend:
        Force ``"openai"`` or ``"claude_cli"``; auto-detected from the API key
        when ``None``.
    max_tokens:
        Maximum tokens in the completion.
    temperature:
        Sampling temperature.
    timeout_sec:
        Per-request timeout in seconds.
    max_retries:
        Maximum number of retry attempts on transient errors.

    Raises
    ------
    RuntimeError
        On non-retryable errors or after exhausting all retries.

    Returns
    -------
    dict
        Keys: ``content`` (str), ``model`` (str), ``usage`` (dict),
        ``finish_reason`` (str).
    """
    # A caller may pass a fully-rendered prompt (e.g. the regression-sweep
    # adjudicator); otherwise render the standard rubric prompt from the input.
    if prompt is None:
        from .input_builder import render_judge_prompt
        prompt = render_judge_prompt(judge_input)
    resolved_backend = _resolve_backend(backend, api_key)

    if resolved_backend == "claude_cli":
        # Ambient Claude auth (the claude CLI), no API key -- same as the agent.
        resolved_model = model or os.environ.get("KARMA_JUDGE_MODEL") or _DEFAULT_CLAUDE_MODEL
        if resolved_model.startswith("gpt-"):  # an OpenAI default carried over
            resolved_model = _DEFAULT_CLAUDE_MODEL

        def _call() -> dict[str, Any]:
            return _call_claude_cli(prompt, resolved_model, timeout_sec)
    else:
        resolved_model = model or os.environ.get("KARMA_JUDGE_MODEL") or _DEFAULT_MODEL
        client = _build_client(
            base_url=base_url, api_key=api_key, timeout_sec=timeout_sec
        )

        def _call() -> dict[str, Any]:
            response = client.chat.completions.create(
                model=resolved_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            choice = response.choices[0]
            return {
                "content": choice.message.content or "",
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "finish_reason": choice.finish_reason,
            }

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return _call()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(_RETRY_BASE_DELAY_SEC * (2 ** attempt))

    raise RuntimeError(
        f"judge LLM call failed after {max_retries + 1} attempts: {last_exc}"
    ) from last_exc
