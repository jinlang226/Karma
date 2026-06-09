"""Case-created namespace cleanup in the k8s environment."""
import subprocess
from unittest.mock import patch
from pathlib import Path

from karma.environments.k8s import K8sEnvironment, _PROTECTED_NAMESPACES


def _ns_output(names):
    return subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="".join(f"namespace/{n}\n" for n in names), stderr="",
    )


def test_cleanup_created_deletes_only_new_nonprotected(tmp_path):
    env = K8sEnvironment()
    baseline = {"karma-run-default", "default", "kube-system"}
    # after the run: a case created `mongodb`; role + system ns still present
    current = _ns_output(["default", "kube-system", "karma-run-default", "mongodb"])
    deleted_args = []

    def fake_kubectl(args, *, check=True, timeout=60):
        if args[:3] == ["get", "namespaces", "-o"]:
            return current
        deleted_args.append(args)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(env, "_kubectl", side_effect=fake_kubectl):
        deleted = env.cleanup_created_namespaces(baseline, run_dir=tmp_path)

    assert deleted == ["mongodb"]
    assert any("mongodb" in a for a in deleted_args)
    # never touches protected or baseline namespaces
    assert not any("kube-system" in a or "default" in a for a in deleted_args)


def test_protected_namespaces_never_deleted(tmp_path):
    env = K8sEnvironment()
    current = _ns_output(list(_PROTECTED_NAMESPACES) + ["spark-pi"])
    deleted = []

    def fake_kubectl(args, *, check=True, timeout=60):
        if args[:3] == ["get", "namespaces", "-o"]:
            return current
        deleted.append(args[2])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(env, "_kubectl", side_effect=fake_kubectl):
        env.cleanup_created_namespaces(set(), run_dir=tmp_path)
    assert deleted == ["spark-pi"]
