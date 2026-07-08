# Persistent Agent Sessions

By default KARMA keeps **one persistent agent conversation across all stages**
of a workflow (`agent_session: persistent`). Each stage resumes the same
CLI/API session, so the agent carries its own earlier reasoning, tool calls,
and results forward rather than starting cold each time.

Setting `agent_session: per_stage` opts out and launches a **fresh agent each
stage**. In that mode the agent has no memory of earlier stages; the
`prompt_mode` (`concat_stateful`/`concat_blind`) re-feeds prior stage *prompts*
as text to compensate, but the agent never sees its own earlier reasoning or
tool output. Only the cluster state carries over.

```yaml
metadata:
  id: my-workflow
spec:
  agent_session: per_stage     # persistent (default) | per_stage
  prompt_mode: progressive     # recommended with persistent: the live session
                               # already holds the history, so don't re-feed it
  stages:
    - { id: stage_01, service: mongodb, case: deploy }
    - { id: stage_02, service: mongodb, case: user-management }
    - { id: stage_03, service: mongodb, case: password-rotation }
```

## How it works

When `persistent`, the runtime mints one session id per run and passes a small
env contract to every stage's agent (`karma/runtime/case.py:_session_env_and_mounts`):

| Var | Meaning |
| --- | --- |
| `BENCH_SESSION_PERSIST` | `1` when persistent (absent otherwise) |
| `BENCH_SESSION_ID` | stable per-run session id (a UUID) |
| `BENCH_SESSION_STAGE_INDEX` | 0-based stage index (0 = create, >0 = resume) |
| `BENCH_SESSION_DIR` | per-run session store the entrypoints read/write |

Each agent entrypoint resumes its own session:

- **claude** — stage 0 runs `claude --session-id <id>`; later stages
  `claude --resume <id>`. claude keys sessions by working directory and KARMA
  gives each stage a different stage dir, so the entrypoint copies the prior
  stage's session JSONL into this stage's project dir before resuming (a no-op
  in docker, where cwd is always `/workspace`).
- **api** — reloads its message transcript from
  `$BENCH_SESSION_DIR/api-messages.json`, appends the new stage prompt, and
  saves the updated transcript at the end.
- **codex** — points `CODEX_HOME` at a per-run dir (seeded with the host
  auth/config) so `codex exec resume --last` resumes only this run's session.
- **copilot** — reuses a stable `--session-id <id>` across stages.

## Sandboxes

- **local** — the host CLI home dirs (`~/.claude`, `~/.codex`, `~/.copilot`)
  already persist across stage subprocesses; only the api transcript and codex
  `CODEX_HOME` need the per-run dir under the run directory.
- **docker** — each stage is a fresh container, so the runtime bind-mounts the
  per-run store into the container at the paths each CLI uses
  (`/root/.claude/projects`, `/root/.copilot`, `/session` for the api transcript
  and `CODEX_HOME`).

## Notes and limitations

- With `persistent` (the default), prefer `prompt_mode: progressive` — the
  session already carries the history, so concat modes just double-feed it.
- **Retries**: a retried stage resumes the same session, so a failed attempt's
  messages stay in the transcript. (A per-attempt `--fork-session` is a possible
  future refinement.)
- **Verified**: claude (local, end-to-end recall across stages) and the api
  agent (transcript resume) are verified. codex and copilot are wired to their
  documented resume flags and validated structurally; confirm with a live
  multi-stage run before relying on them.
- The `claude` entrypoint mirrors claude's cwd→project-dir slug
  (`[^a-zA-Z0-9]` → `-`); if a future CLI version changes that scheme, the
  local copy-forward would need updating (docker is unaffected).
