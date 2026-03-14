# Run Profiles

This directory contains reusable execution presets for `orchestrator.py --profile`.

Usage:

```bash
python3 orchestrator.py workflow-run \
  --profile profiles/debug.yaml \
  --workflow workflows/workflow-demo.yaml
```

Rules:

- Profiles are regular YAML or JSON files.
- CLI flags override profile values.
- `command:` is recommended so a profile fails fast if used with the wrong subcommand.
- These are run profiles only. They are different from judge rubric profiles under `resources/*/judge_base.yaml`.

Included files:

- `debug.yaml`: manual Docker debugging profile. It starts `cli-runner` and keeps the container alive with `sleep 86400`.
- `codex.yaml`: Codex workflow profile using `cli-runner` and the baked `/opt/agent/system_prompt.txt`.

For Codex:

- The default auth path is `~/.codex/auth.json`.
- If your auth lives somewhere else, update `agent_auth_path`.
- You still pass `--workflow ...` on the CLI unless you want to hardcode a workflow into the profile.
