# CLI Runner (Codex/Claude)

This image provides a minimal sandbox with the kubectl wrapper + proxy kubeconfig so you can run CLI agents non‑interactively.
It does **not** run an agent by default; you provide `--agent-cmd` when launching via the orchestrator.

## Baked prompt

Default system prompt is baked at:

```
/opt/agent/system_prompt.txt
```

You can concatenate it with the case prompt (`/workspace/PROMPT.md`) at runtime.

## One‑shot Codex run (non‑interactive)

This avoids the TUI and works headlessly via `codex exec`:

```bash
python3 orchestrator.py run \
  --sandbox docker \
  --agent cli-runner \
  --agent-build \
  --service nginx-ingress \
  --case ingress_route_ready \
  --max-attempts 1 \
  --agent-auth-path ~/.codex/auth.json \
  --agent-cmd 'bash -c "cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -C /workspace --skip-git-repo-check \"$(cat /tmp/codex_prompt.txt)\""'
```

Notes:
- `codex exec` is the **non‑interactive** mode.
- `--dangerously-bypass-approvals-and-sandbox` is required to bypass Codex’s internal Landlock sandbox (container already isolates).
  Do **not** combine it with `-a never`.
- `--skip-git-repo-check` is required because `/workspace` is not a git repo.

## Workflow run (RabbitMQ style)

Use `workflow-run` for multi-stage workflows:

```bash
python3 orchestrator.py workflow-run \
  --sandbox docker \
  --agent cli-runner \
  --agent-build \
  --workflow workflows/rabbitmq-upgrade-tls-migration-a-to-b.yaml \
  --agent-auth-path ~/.codex/auth.json \
  --agent-cmd 'bash -c "cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -C /workspace --skip-git-repo-check \"$(cat /tmp/codex_prompt.txt)\""'
```

Workflow prompt semantics:
- `/workspace/PROMPT.md` includes execution protocol and feedback-file rules.
- Submit by creating `/workspace/submit.signal`.
- Wait for `/workspace/submit.ack` before trusting `/workspace/submit_result.json`.
- Use `workflow.continue`, `can_retry`, and `workflow.final` in `submit_result.json` for control flow.
- Keep the top-level agent process alive until `workflow.final=true` or another terminal result; do not background the real workflow logic and exit the parent process.

## Auth mounting (browser login tokens)

If you logged in via browser, Codex stores tokens in `~/.codex/auth.json`. Use:

```
--agent-auth-path ~/.codex/auth.json
```

The orchestrator copies that file to a temp dir, makes it writable, and mounts it into the container so tokens can refresh.

## Claude Code

Claude Code is also installed in this image. If it uses a local config/token file,
mount it with `--agent-auth-path` (use `--agent-auth-dest` if you need a custom container path).
