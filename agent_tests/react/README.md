# ReAct Agent

Minimal ReAct-style agent wrapper for the benchmark.

This folder contains:
- Dockerfile
- run_agent.py
- entrypoint.sh
- config.env.example

Expected behavior:
- Read /workspace/PROMPT.md
- Use kubectl via PATH
- Submit by creating `$BENCHMARK_SUBMIT_FILE` (for example: `touch "$BENCHMARK_SUBMIT_FILE"`).
- After submit, wait for `submit.signal` to be removed and `submit.ack` to appear.
- Only read `$BENCHMARK_SUBMIT_RESULT_FILE` after `submit.ack` appears (earlier content may be stale).
- Retry when `can_retry` is true.
- For workflows: if `workflow.continue` is true, re-read `/workspace/PROMPT.md` and continue; if `workflow.final` is true, stop.

Configuration:
- `config.env` is auto-loaded by the orchestrator when using `--agent react`.
- Required: `LLM_MODEL`, `LLM_API_KEY`
- Optional: `LLM_BASE_URL`, `REACT_STEP_DELAY_SEC`, `REACT_MAX_STEPS`,
  `BENCHMARK_SUBMIT_RESULT_TIMEOUT` (seconds)

Quick start:
```bash
docker build -t benchmark-react -f agent_tests/react/Dockerfile .

cp agent_tests/react/config.env.example agent_tests/react/config.env
# edit agent_tests/react/config.env with your LLM settings

python3 orchestrator.py run \
  --sandbox docker \
  --docker-image benchmark-react \
  --service nginx-ingress \
  --case ingress_class_routing
```
