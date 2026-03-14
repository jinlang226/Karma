from pathlib import Path
from tempfile import TemporaryDirectory

from app.llm_config import collect_judge_env, collect_llm_env, parse_env_file


def test_parse_env_file_supports_export_and_quotes():
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "config.env"
        path.write_text(
            "\n".join(
                [
                    "# comment",
                    "export LLM_MODEL=gpt-4o-mini",
                    "LLM_API_KEY='abc123'",
                    'LLM_BASE_URL="https://example.com/v1"',
                    "INVALID_LINE",
                ]
            ),
            encoding="utf-8",
        )

        parsed = parse_env_file(path)
        assert parsed["LLM_MODEL"] == "gpt-4o-mini"
        assert parsed["LLM_API_KEY"] == "abc123"
        assert parsed["LLM_BASE_URL"] == "https://example.com/v1"
        assert "INVALID_LINE" not in parsed


def test_collect_llm_env_prefers_explicit_file_and_overrides_with_process_env():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        explicit_path = root / "explicit.env"
        explicit_path.write_text("LLM_MODEL=from-file\nLLM_API_KEY=file-key\n", encoding="utf-8")

        env = {"LLM_MODEL": "from-env", "OPENAI_API_KEY": "openai-key"}
        merged = collect_llm_env(
            llm_env_file=str(explicit_path),
            repo_root=root,
            agent="react",
            environ=env,
        )

        assert merged["LLM_MODEL"] == "from-env"
        assert merged["LLM_API_KEY"] == "file-key"
        assert merged["OPENAI_API_KEY"] == "openai-key"


def test_collect_llm_env_auto_loads_agent_config():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config_path = root / "agent_tests" / "react" / "config.env"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("LLM_MODEL=auto-model\nLLM_API_KEY=auto-key\n", encoding="utf-8")

        merged = collect_llm_env(
            llm_env_file=None,
            repo_root=root,
            agent="react",
            environ={},
        )

        assert merged["LLM_MODEL"] == "auto-model"
        assert merged["LLM_API_KEY"] == "auto-key"


def test_collect_judge_env_prefers_judge_env_default():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "judge.env").write_text(
            "JUDGE_MODEL=j-model\nJUDGE_API_KEY=j-key\n",
            encoding="utf-8",
        )
        merged, source = collect_judge_env(
            judge_env_file=None,
            repo_root=root,
            environ={},
            return_source=True,
        )
        assert merged["JUDGE_MODEL"] == "j-model"
        assert merged["JUDGE_API_KEY"] == "j-key"
        assert source and source.endswith("judge.env")


def test_collect_judge_env_legacy_fallback_when_no_judge_env():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        legacy = root / "agent_tests" / "react" / "config.env"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("LLM_MODEL=legacy-model\nLLM_API_KEY=legacy-key\n", encoding="utf-8")
        merged, source = collect_judge_env(
            judge_env_file=None,
            repo_root=root,
            environ={},
            return_source=True,
        )
        assert merged["LLM_MODEL"] == "legacy-model"
        assert source and source.startswith("legacy:")
