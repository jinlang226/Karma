from pathlib import Path


LLM_ENV_KEYS = (
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_TEMPERATURE",
)

JUDGE_ENV_KEYS = (
    "JUDGE_MODEL",
    "JUDGE_API_KEY",
    "JUDGE_BASE_URL",
)


def parse_env_file(path):
    env_vars = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        env_vars[key] = value
    return env_vars


def collect_llm_env(llm_env_file=None, repo_root=None, agent="react", environ=None):
    env = environ or {}
    repo_path = Path(repo_root).resolve() if repo_root is not None else None

    env_vars = {}
    if llm_env_file:
        env_vars.update(parse_env_file(llm_env_file))
    elif repo_path is not None:
        config_path = repo_path / "agent_tests" / str(agent or "react") / "config.env"
        if config_path.exists():
            env_vars.update(parse_env_file(config_path))

    for key in LLM_ENV_KEYS + JUDGE_ENV_KEYS + ("OPENAI_API_KEY",):
        if env.get(key):
            env_vars[key] = env[key]
    return env_vars


def collect_judge_env(
    judge_env_file=None,
    repo_root=None,
    environ=None,
    allow_legacy_agent_env=True,
    legacy_agent="react",
    return_source=False,
):
    env = environ or {}
    repo_path = Path(repo_root).resolve() if repo_root is not None else None

    env_vars = {}
    source = None

    if judge_env_file:
        env_vars.update(parse_env_file(judge_env_file))
        source = str(judge_env_file)
    elif repo_path is not None:
        default_paths = (
            repo_path / "judge.env",
            repo_path / "config" / "judge.env",
        )
        for path in default_paths:
            if path.exists():
                env_vars.update(parse_env_file(path))
                source = str(path)
                break

        if source is None and allow_legacy_agent_env:
            legacy_path = repo_path / "agent_tests" / str(legacy_agent or "react") / "config.env"
            if legacy_path.exists():
                env_vars.update(parse_env_file(legacy_path))
                source = f"legacy:{legacy_path}"

    for key in JUDGE_ENV_KEYS + LLM_ENV_KEYS + ("OPENAI_API_KEY",):
        if env.get(key):
            env_vars[key] = env[key]

    if return_source:
        return env_vars, source
    return env_vars
