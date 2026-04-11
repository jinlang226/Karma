# react agent

ReAct-style agent that alternates between reasoning and action steps.

## Files
- `Dockerfile` -- container image definition
- `entrypoint.sh` -- container entrypoint script
- `run_agent.py` -- agent implementation

## Usage
Registered as `react` in `agents/registry.py`.
Launch via `karma run-workflow --agent react ...`
