import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = (
        repo_root
        / "resources"
        / "rabbitmq-experiments"
        / "blue_green_migration"
        / "oracle"
        / "oracle.py"
    )
    spec = importlib.util.spec_from_file_location("blue_green_migration_oracle", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluate_green_batch_accepts_seed_and_live_message():
    mod = _load_module()
    batch = [{"payload": f'{{"id": {i}}}'} for i in range(1, 6)]
    batch.append({"payload": '{"id": 6}'})

    errors = mod.evaluate_green_batch(5, batch)

    assert errors == []


def test_evaluate_green_batch_requires_post_seed_live_message():
    mod = _load_module()
    batch = [{"payload": f'{{"id": {i}}}'} for i in range(1, 6)]

    errors = mod.evaluate_green_batch(5, batch)

    assert errors == ["Expected at least one live post-seed message on green (id > 5) to prove cutover"]


def test_evaluate_green_batch_reports_missing_seed_range_before_live_check():
    mod = _load_module()
    batch = [
        {"payload": '{"id": 1}'},
        {"payload": '{"id": 2}'},
        {"payload": '{"id": 4}'},
        {"payload": '{"id": 5}'},
        {"payload": '{"id": 6}'},
    ]

    errors = mod.evaluate_green_batch(5, batch)

    assert errors == ["Seed range 1..N not fully present on green (missing: 3)"]
