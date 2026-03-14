# Workflow Stage Comparison Sweep

Runs two fixed workflows repeatedly and compares outcome stability:

- single workflow (default: `workflows/mongodb-rbac-reset-script-single.yaml`)
- three-stage workflow (default: `workflows/mongodb-rbac-reset-script-after-rbac-setup.yaml`)

Default repeat count is 10 runs per workflow.

## Example

```bash
. .venv/bin/activate
python scripts/workflow_stage_comparison_sweep.py \
  --runs-per-workflow 10 \
  --sandbox docker \
  --orchestrator-arg=--agent \
  --orchestrator-arg=cli-runner \
  --orchestrator-arg=--agent-build \
  --orchestrator-arg=--agent-auth-path \
  --orchestrator-arg="$HOME/.codex/auth.json" \
  --orchestrator-arg=--agent-cmd \
  --orchestrator-arg='bash -c "cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -C /workspace --skip-git-repo-check \"$(cat /tmp/codex_prompt.txt)\""'
```

## Quick smoke (no execution)

```bash
. .venv/bin/activate
python scripts/workflow_stage_comparison_sweep.py --runs-per-workflow 1 --dry-run
```

## Resume semantics

Use the same `--work-dir` and add `--resume` to continue unfinished runs:

```bash
. .venv/bin/activate
python scripts/workflow_stage_comparison_sweep.py \
  --runs-per-workflow 50 \
  --work-dir ".benchmark/workflow-stage-comparison-20260310T203429Z" \
  --resume \
  ...
```

Behavior:

- Resume loads existing `history.jsonl`.
- For each workflow kind (`single`, `three_stage`), it starts from `max(attempt_index)+1`.
- It only runs remaining attempts up to `--runs-per-workflow`.
- Existing history/log entries are preserved; new results are appended.

## Outputs

Under `--work-dir` (default `.benchmark/workflow-stage-comparison-<utc-stamp>`):

- `history.jsonl`: one JSON record per run
- `aggregate_runs.csv`: flat row-per-run table
- `summary.json`: combined summary and comparison section
- `logs/*.log`: raw orchestrator stdout/stderr per run

## What summary.json includes

- pass/fail counts and pass rate per workflow
- terminal reason and classification counts
- failed stage frequency
- average/median failed stage index
- average/median stage reached
- comparison deltas (three-stage minus single)
