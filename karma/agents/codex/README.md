# Codex agent

Runs OpenAI's Codex CLI headlessly as a KARMA agent (docker sandbox mode).

The entrypoint reads the task from `/workspace/prompt.txt`, runs
`codex ... exec` against the cluster (kubectl flows through KARMA's proxy via
the mounted kubeconfig), and writes `/workspace/submit.txt` when done.

## Build

```
python orchestrator.py run-case demo configmap-update \
  --agent codex --sandbox docker --agent-build \
  --agent-auth-path ~/.codex/auth.json --agent-auth-dest /root/.codex/auth.json
```

`--agent-build` builds `karma-agent-codex:latest` from this folder's Dockerfile.

## Auth

Either mount your Codex credentials file
(`--agent-auth-path ~/.codex/auth.json --agent-auth-dest /root/.codex/auth.json`)
or set `OPENAI_API_KEY` on the host (KARMA forwards it into the container).
Set `CODEX_MODEL` to pin a model; otherwise the Codex CLI default is used.
