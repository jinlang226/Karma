# api agent

A self-contained **agentic loop over an OpenAI-compatible chat-completions API**,
defaulting to **DeepSeek**. Unlike claude_code/codex/copilot it wraps no external
CLI — `run_agent.py` is the whole agent: it reads `prompt.txt`, drives the model
through a single `bash` tool, runs the model's commands (kubectl, etc.) against
the cluster via KARMA's injected `KUBECONFIG` proxy, and writes `submit.txt` when
the model returns a final answer. Pure stdlib (urllib/json/subprocess) — no
dependencies to install.

Because DeepSeek's API is OpenAI-compatible, the same loop also targets OpenAI or
any compatible endpoint by changing the env below.

## Config (environment)

| var | default | meaning |
|-----|---------|---------|
| `KARMA_API_BASE_URL` | `https://api.deepseek.com` | API base (POSTs to `<base>/chat/completions`) |
| `KARMA_API_KEY` | — | API key; falls back to `DEEPSEEK_API_KEY`, then `OPENAI_API_KEY` |
| `KARMA_API_MODEL` | `deepseek-chat` | model id (e.g. `deepseek-reasoner`, or `gpt-4o` for OpenAI) |
| `KARMA_API_MAX_STEPS` | `40` | max tool-use iterations before forced submit |

## Auth

- **local** sandbox: export `DEEPSEEK_API_KEY` (or `KARMA_API_KEY`) in your shell.
- **docker** sandbox: same env — KARMA forwards `KARMA_API_KEY`, `DEEPSEEK_API_KEY`,
  `KARMA_API_BASE_URL`, `KARMA_API_MODEL` into the container (`karma/sandbox.py`).

## Run

```
# DeepSeek (default):
DEEPSEEK_API_KEY=sk-... python orchestrator.py run-case <service> <case> --agent api --sandbox local

# point at OpenAI instead:
KARMA_API_BASE_URL=https://api.openai.com/v1 KARMA_API_MODEL=gpt-4o \
  OPENAI_API_KEY=sk-... python orchestrator.py run-case <service> <case> --agent api --sandbox local

# docker (build the image first):
DEEPSEEK_API_KEY=sk-... python orchestrator.py run-case <service> <case> --agent api --sandbox docker --agent-build
```

> Note: confirm DeepSeek's exact chat-completions path for your account — some
> OpenAI-compatible providers serve it at `<base>/v1/chat/completions`. If so,
> set `KARMA_API_BASE_URL=https://api.deepseek.com/v1`.
