"""Shared pytest fixtures for the KARMA test suite."""

import pytest
from pathlib import Path


@pytest.fixture()
def resources_dir(tmp_path) -> Path:
    """Return a temporary resources directory pre-populated with a demo case."""
    case_dir = tmp_path / "demo" / "configmap-update"
    case_dir.mkdir(parents=True)
    (case_dir / "test.yaml").write_text(
        "prompt: Update the configmap value.\n"
        "params:\n"
        "  target_key:\n"
        "    default: my-key\n"
        "oracle:\n"
        "  verify:\n"
        "    commands:\n"
        "      - command: kubectl get configmap target -o yaml\n"
    )
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path) -> Path:
    """Return a clean temporary run directory."""
    return tmp_path
