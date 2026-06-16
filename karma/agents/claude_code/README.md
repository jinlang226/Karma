# claude_code agent

Runs Anthropic's **Claude Code** CLI (`claude`) as a KARMA agent. In `local`
sandbox mode the host `claude` binary is invoked as a subprocess; it reads the
task from `prompt.txt` and writes its final answer to `submit.txt`.

## Files
- `entrypoint.sh` -- local-sandbox entrypoint: invokes `claude --print` on the
  stage prompt and atomically writes `submit.txt` when done.
- `Dockerfile` -- placeholder image for future `docker` sandbox mode (unused in
  local mode).
- `README.md` -- this file.

## Requirements
- An authenticated host `claude` CLI on `PATH` (credentials in `~/.claude`).
- Optional `KARMA_CLAUDE_AGENT_MODEL` env var to override the model
  (default `sonnet`).

## Usage
Registered as `claude_code` in `agents/registry.py`.

```bash
python orchestrator.py run-case demo configmap-update \
  --agent claude_code --sandbox local
```

The agent operates the cluster via `kubectl` (its Bash tool), which is routed
through KARMA's kubectl proxy using the injected `KUBECONFIG`. It runs with
`--dangerously-skip-permissions` so it can act autonomously inside the
benchmark's ephemeral namespaces.
