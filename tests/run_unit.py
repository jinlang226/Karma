#!/usr/bin/env python3
"""
Minimal unit test runner.

We intentionally avoid adding pytest as a dependency. Tests are plain Python
functions that use `assert` and are named `test_*` in `tests/unit/test_*.py`.
"""

import importlib.util
import inspect
import os
import shutil
import sys
import time
from pathlib import Path


def _import_module(path: Path):
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    auto_runs_subdir = False
    if not os.environ.get("BENCHMARK_RUNS_SUBDIR"):
        auto_runs_subdir = True
        os.environ["BENCHMARK_RUNS_SUBDIR"] = (
            f".benchmark/test_runs/unit_{int(time.time())}_{os.getpid()}"
        )
    runs_root = (repo_root / os.environ["BENCHMARK_RUNS_SUBDIR"]).resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    unit_dir = repo_root / "tests" / "unit"
    paths = sorted(unit_dir.glob("test_*.py"))
    if not paths:
        print("[unit] no tests found")
        return 0

    failures = 0
    total = 0
    for path in paths:
        module = _import_module(path)
        tests = []
        for name, obj in vars(module).items():
            if not name.startswith("test_"):
                continue
            if not callable(obj):
                continue
            if inspect.isclass(obj):
                continue
            tests.append((name, obj))
        for name, fn in sorted(tests, key=lambda x: x[0]):
            total += 1
            try:
                fn()
            except Exception as exc:
                failures += 1
                print(f"[unit] FAIL {path.name}::{name}: {exc}")
    if failures:
        print(f"[unit] {failures}/{total} tests failed")
        if auto_runs_subdir:
            print(f"[unit] test artifacts kept at: {runs_root}")
        return 1
    print(f"[unit] ok ({total} tests)")
    keep_runs = os.environ.get("BENCHMARK_TEST_KEEP_RUNS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if auto_runs_subdir and not keep_runs:
        shutil.rmtree(runs_root, ignore_errors=True)
    elif auto_runs_subdir:
        print(f"[unit] test artifacts kept at: {runs_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
