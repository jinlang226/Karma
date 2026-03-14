# Agent Integration Contract

This benchmark provides an isolated agent bundle mounted at `/workspace`.

Required inputs (read-only):
- `/workspace/PROMPT.md`
- `/workspace/runbooks/` (optional)
- `/workspace/env.sh` (helper export script)

Required environment variables:
- `KUBECONFIG`: path to proxy kubeconfig
- `BENCHMARK_ACTION_TRACE_LOG`: path to action trace JSONL
- `BENCHMARK_SUBMIT_FILE`: file path to touch for submission
- `BENCHMARK_SUBMIT_URL`: optional HTTP endpoint for submission

Required output:
- Signal completion by `touch $BENCHMARK_SUBMIT_FILE`
  - If `BENCHMARK_SUBMIT_URL` is set, you may also POST to it.

Allowed tools:
- `kubectl` is in PATH (wrapper logs command intent)
- Standard shell utilities in the agent image

Do not read the benchmark repo. The agent container should only access
`/workspace` and `/run` (mounted by the orchestrator).
