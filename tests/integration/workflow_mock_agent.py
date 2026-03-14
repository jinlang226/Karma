#!/usr/bin/env python3
"""
Compatibility shim for workflow mock agent.

The canonical fixture now lives at:
tests/fixtures/workflow_mock/agents/workflow_mock_agent.py
"""

from pathlib import Path
import runpy


TARGET = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "workflow_mock"
    / "agents"
    / "workflow_mock_agent.py"
)


if __name__ == "__main__":
    runpy.run_path(str(TARGET), run_name="__main__")
