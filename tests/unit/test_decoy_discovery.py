"""Unit tests for decoy directory auto-discovery."""

from karma.definitions.cases import discover_case_decoys


def test_discovers_yaml_manifests_in_decoy_dir(tmp_path):
    d = tmp_path / "svc" / "case" / "decoy"
    d.mkdir(parents=True)
    (d / "a.yaml").write_text("kind: Secret")
    (d / "b.yaml").write_text("kind: ConfigMap")
    (d / "notes.txt").write_text("ignored: not a manifest")

    out = discover_case_decoys(tmp_path, "svc", "case")
    paths = sorted(c["path"] for c in out)
    assert paths == ["svc/case/decoy/a.yaml", "svc/case/decoy/b.yaml"]
    # manifests carry their own namespace, so no override is set
    assert all(c["namespace"] == "" for c in out)


def test_no_decoy_dir_returns_empty(tmp_path):
    (tmp_path / "svc" / "case").mkdir(parents=True)
    assert discover_case_decoys(tmp_path, "svc", "case") == []
