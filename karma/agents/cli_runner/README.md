# cli_runner agent

CLI-driven agent for scripted or solver-based runs. Reads the prompt from
the KARMA_PROMPT_PATH environment variable and executes a solver script.

## Files
- `Dockerfile` -- container image definition
- `entrypoint.sh` -- container entrypoint script
- `system_prompt.txt` -- default system prompt template

## Usage
Registered as `cli_runner` in `agents/registry.py`.
Launch via `karma run-workflow --agent cli_runner ...`
