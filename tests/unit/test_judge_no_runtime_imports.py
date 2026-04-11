"""Verify that judge/* modules do not import runtime.*."""

import ast
import importlib
from pathlib import Path


_JUDGE_DIR = Path(__file__).parent.parent.parent / "karma" / "judge"
_FORBIDDEN_PREFIX = "karma.runtime"


def _collect_imports(source: str) -> list[str]:
    """Return all module names imported in *source*."""
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def test_judge_engine_does_not_import_runtime():
    source = (_JUDGE_DIR / "engine.py").read_text()
    for imp in _collect_imports(source):
        assert not imp.startswith(_FORBIDDEN_PREFIX), (
            f"judge/engine.py imports {imp!r}, which violates the "
            f"judge/* must not import runtime/* rule."
        )


def test_judge_scoring_does_not_import_runtime():
    source = (_JUDGE_DIR / "scoring.py").read_text()
    for imp in _collect_imports(source):
        assert not imp.startswith(_FORBIDDEN_PREFIX)


def test_judge_rubric_does_not_import_runtime():
    source = (_JUDGE_DIR / "rubric.py").read_text()
    for imp in _collect_imports(source):
        assert not imp.startswith(_FORBIDDEN_PREFIX)


def test_judge_client_does_not_import_runtime():
    source = (_JUDGE_DIR / "client.py").read_text()
    for imp in _collect_imports(source):
        assert not imp.startswith(_FORBIDDEN_PREFIX)


def test_judge_input_builder_does_not_import_runtime():
    source = (_JUDGE_DIR / "input_builder.py").read_text()
    for imp in _collect_imports(source):
        assert not imp.startswith(_FORBIDDEN_PREFIX)
