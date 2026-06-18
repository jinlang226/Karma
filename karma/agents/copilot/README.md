# copilot agent

Wraps the **GitHub Copilot CLI** (`@github/copilot`, the agentic `copilot`
command) as a KARMA agent. Copilot reads the task from `prompt.txt`, acts on the
cluster through its shell tool (running `kubectl` against KARMA's kubectl proxy,
which the injected `KUBECONFIG` points at), and KARMA records completion when
`submit.txt` appears.

The entrypoint runs Copilot headless and full-auto:

```
copilot --prompt "<task>" --allow-all [--model <model>]
```

- `--prompt` runs non-interactively (no chat session).
- `--allow-all` auto-approves every tool/path/URL so kubectl runs without a
  permission prompt (the Copilot analogue of claude_code's
  `--dangerously-skip-permissions`).
- `--model` is set from `KARMA_COPILOT_AGENT_MODEL` when present (e.g.
  `gpt-5.2`); omit it to use Copilot's default.

## Auth

- **local** sandbox: uses the host's Copilot login. Sign in once with
  `copilot` (interactive) or `gh auth login`; or export `GITHUB_TOKEN`.
- **docker** sandbox: set `GITHUB_TOKEN` in your environment — KARMA forwards it
  into the container (`karma/sandbox.py`). The host interactive login is not
  forwarded.

## Run

```
python orchestrator.py run-case <service> <case> --agent copilot --sandbox local
# docker (build the image first):
python orchestrator.py run-case <service> <case> --agent copilot --sandbox docker --agent-build
```
