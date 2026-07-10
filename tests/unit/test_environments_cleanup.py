"""Case-created namespace cleanup in the k8s environment."""
import subprocess
from unittest.mock import patch
from pathlib import Path

from karma.environments.k8s import K8sEnvironment, _PROTECTED_NAMESPACES


def test_cleanup_created_deletes_only_recorded_nonprotected(tmp_path):
    env = K8sEnvironment()
    # SS-2: the caller passes the EXACT set this run created; the function deletes
    # only those (minus protected), and never re-diffs the live namespace list --
    # so a concurrent run's namespaces are never swept up. Include some protected
    # names in the recorded set to prove they are still excluded defensively.
    created = {"mongodb", "cockroachdb", "default", "kube-system"}
    deleted_args = []

    def fake_kubectl(args, *, check=True, timeout=60):
        # A live-list call would mean the function still re-diffs -- it must not.
        assert args[:3] != ["get", "namespaces", "-o"], "must not re-list namespaces"
        deleted_args.append(args)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(env, "_kubectl", side_effect=fake_kubectl):
        deleted = env.cleanup_created_namespaces(created, run_dir=tmp_path)

    assert deleted == ["cockroachdb", "mongodb"]
    assert any("mongodb" in a for a in deleted_args)
    assert any("cockroachdb" in a for a in deleted_args)
    # never touches protected namespaces even when they are in the recorded set
    assert not any("kube-system" in a or "default" in a for a in deleted_args)


def test_empty_created_set_deletes_nothing(tmp_path):
    env = K8sEnvironment()
    deleted = []

    def fake_kubectl(args, *, check=True, timeout=60):
        deleted.append(args)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(env, "_kubectl", side_effect=fake_kubectl):
        result = env.cleanup_created_namespaces(set(), run_dir=tmp_path)
    assert result == [] and deleted == []


def test_protected_namespaces_never_deleted(tmp_path):
    env = K8sEnvironment()
    created = set(_PROTECTED_NAMESPACES) | {"spark-pi"}
    deleted = []

    def fake_kubectl(args, *, check=True, timeout=60):
        deleted.append(args[2])
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with patch.object(env, "_kubectl", side_effect=fake_kubectl):
        env.cleanup_created_namespaces(created, run_dir=tmp_path)
    assert deleted == ["spark-pi"]
