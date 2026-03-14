import importlib.util
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = (
        repo_root
        / "resources"
        / "rabbitmq-experiments"
        / "blue_green_migration"
        / "setup_precondition_check.py"
    )
    spec = importlib.util.spec_from_file_location("blue_green_migration_setup_check", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to import {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_target_app_client_quiesced_absent_deployment():
    mod = _load_module()
    errors = []

    def _missing(*_args, **_kwargs):
        raise RuntimeError("not found")

    orig_run_json = mod.run_json
    orig_list_pods = mod.list_pods
    mod.run_json = _missing
    mod.list_pods = lambda *_args, **_kwargs: []
    try:
        mod._check_target_app_client_quiesced("ns", errors)
    finally:
        mod.run_json = orig_run_json
        mod.list_pods = orig_list_pods

    assert errors == []


def test_target_app_client_quiesced_clean_state():
    mod = _load_module()
    errors = []

    deploy = {
        "spec": {"replicas": 0},
        "status": {"readyReplicas": 0},
    }
    orig_run_json = mod.run_json
    orig_list_pods = mod.list_pods
    mod.run_json = lambda *_args, **_kwargs: deploy
    mod.list_pods = lambda *_args, **_kwargs: []
    try:
        mod._check_target_app_client_quiesced("ns", errors)
    finally:
        mod.run_json = orig_run_json
        mod.list_pods = orig_list_pods

    assert errors == []


def test_target_app_client_quiesced_detects_live_publishers():
    mod = _load_module()
    errors = []

    deploy = {
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 1},
    }
    pods = [
        {"metadata": {"name": "app-client-abc"}, "status": {"phase": "Running"}},
        {"metadata": {"name": "app-client-def"}, "status": {"phase": "Pending"}},
    ]
    orig_run_json = mod.run_json
    orig_list_pods = mod.list_pods
    mod.run_json = lambda *_args, **_kwargs: deploy
    mod.list_pods = lambda *_args, **_kwargs: pods
    try:
        mod._check_target_app_client_quiesced("ns", errors)
    finally:
        mod.run_json = orig_run_json
        mod.list_pods = orig_list_pods

    assert any("replicas should be 0" in e for e in errors)
    assert any("readyReplicas should be 0" in e for e in errors)
    assert any("pods still active" in e for e in errors)


def test_seed_id_coverage_prefers_curl_on_rabbitmq_4():
    mod = _load_module()
    errors = []
    calls = []

    orig_read_secret_value = mod._read_secret_value
    orig_detect_major = mod._detect_rabbitmq_major
    orig_fetch_python = mod._fetch_seed_batch_via_python
    orig_fetch_curl = mod._fetch_seed_batch_via_curl
    mod._read_secret_value = (
        lambda *_args, **_kwargs: "admin" if _args[2] == "username" else "adminpass"
    )
    mod._detect_rabbitmq_major = lambda *_args, **_kwargs: 4
    mod._fetch_seed_batch_via_python = lambda *_args, **_kwargs: calls.append("python")
    mod._fetch_seed_batch_via_curl = (
        lambda *_args, **_kwargs: calls.append("curl")
        or [{"payload": "{\"id\": 1}"}, {"payload": "{\"id\": 2}"}, {"payload": "{\"id\": 3}"}]
    )
    try:
        mod._check_seed_id_coverage("ns", "rabbitmq", 3, errors)
    finally:
        mod._read_secret_value = orig_read_secret_value
        mod._detect_rabbitmq_major = orig_detect_major
        mod._fetch_seed_batch_via_python = orig_fetch_python
        mod._fetch_seed_batch_via_curl = orig_fetch_curl

    assert errors == []
    assert calls == ["curl"]


def test_seed_id_coverage_falls_back_to_curl_when_python_probe_fails():
    mod = _load_module()
    errors = []
    calls = []

    orig_read_secret_value = mod._read_secret_value
    orig_detect_major = mod._detect_rabbitmq_major
    orig_fetch_python = mod._fetch_seed_batch_via_python
    orig_fetch_curl = mod._fetch_seed_batch_via_curl
    mod._read_secret_value = (
        lambda *_args, **_kwargs: "admin" if _args[2] == "username" else "adminpass"
    )
    mod._detect_rabbitmq_major = lambda *_args, **_kwargs: 3

    def _python_fail(*_args, **_kwargs):
        calls.append("python")
        raise RuntimeError("python missing")

    def _curl_ok(*_args, **_kwargs):
        calls.append("curl")
        return [{"payload": "{\"id\": 1}"}, {"payload": "{\"id\": 2}"}]

    mod._fetch_seed_batch_via_python = _python_fail
    mod._fetch_seed_batch_via_curl = _curl_ok
    try:
        mod._check_seed_id_coverage("ns", "rabbitmq", 2, errors)
    finally:
        mod._read_secret_value = orig_read_secret_value
        mod._detect_rabbitmq_major = orig_detect_major
        mod._fetch_seed_batch_via_python = orig_fetch_python
        mod._fetch_seed_batch_via_curl = orig_fetch_curl

    assert errors == []
    assert calls == ["python", "curl"]


def test_seed_id_coverage_skips_probe_failure_on_rabbitmq_4():
    mod = _load_module()
    errors = []

    orig_read_secret_value = mod._read_secret_value
    orig_detect_major = mod._detect_rabbitmq_major
    orig_fetch_python = mod._fetch_seed_batch_via_python
    orig_fetch_curl = mod._fetch_seed_batch_via_curl
    mod._read_secret_value = (
        lambda *_args, **_kwargs: "admin" if _args[2] == "username" else "adminpass"
    )
    mod._detect_rabbitmq_major = lambda *_args, **_kwargs: 4
    mod._fetch_seed_batch_via_python = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("python missing")
    )
    mod._fetch_seed_batch_via_curl = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("curl missing")
    )
    try:
        mod._check_seed_id_coverage("ns", "rabbitmq", 2, errors)
    finally:
        mod._read_secret_value = orig_read_secret_value
        mod._detect_rabbitmq_major = orig_detect_major
        mod._fetch_seed_batch_via_python = orig_fetch_python
        mod._fetch_seed_batch_via_curl = orig_fetch_curl

    assert errors == []


def test_seed_id_coverage_reports_probe_failure_on_rabbitmq_3():
    mod = _load_module()
    errors = []

    orig_read_secret_value = mod._read_secret_value
    orig_detect_major = mod._detect_rabbitmq_major
    orig_fetch_python = mod._fetch_seed_batch_via_python
    orig_fetch_curl = mod._fetch_seed_batch_via_curl
    mod._read_secret_value = (
        lambda *_args, **_kwargs: "admin" if _args[2] == "username" else "adminpass"
    )
    mod._detect_rabbitmq_major = lambda *_args, **_kwargs: 3
    mod._fetch_seed_batch_via_python = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("python missing")
    )
    mod._fetch_seed_batch_via_curl = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("curl missing")
    )
    try:
        mod._check_seed_id_coverage("ns", "rabbitmq", 2, errors)
    finally:
        mod._read_secret_value = orig_read_secret_value
        mod._detect_rabbitmq_major = orig_detect_major
        mod._fetch_seed_batch_via_python = orig_fetch_python
        mod._fetch_seed_batch_via_curl = orig_fetch_curl

    assert errors
    assert "failed to inspect seed id coverage" in errors[0]


def test_seed_state_with_mode_allows_live_traffic_when_not_exact():
    mod = _load_module()
    errors = []

    orig_run = mod.run
    mod.run = lambda *_args, **_kwargs: "app-queue 277\n"
    try:
        mod._check_seed_state_with_mode("ns", "rabbitmq", 50, errors, exact=False)
    finally:
        mod.run = orig_run

    assert errors == []


def test_seed_state_with_mode_rejects_non_exact_seed_count_when_exact():
    mod = _load_module()
    errors = []

    orig_run = mod.run
    mod.run = lambda *_args, **_kwargs: "app-queue 277\n"
    try:
        mod._check_seed_state_with_mode("ns", "rabbitmq", 50, errors, exact=True)
    finally:
        mod.run = orig_run

    assert errors == ["source: app-queue expected exactly 50 message(s), found 277"]
