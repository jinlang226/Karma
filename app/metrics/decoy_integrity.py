import json
from pathlib import Path


def _load_snapshot(path):
    try:
        payload = json.loads(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    items = payload.get("items")
    if not isinstance(items, dict):
        return None, "snapshot missing items"
    return items, None


def _load_decoys(path):
    try:
        payload = json.loads(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    decoys = payload.get("decoys")
    if decoys is None:
        return None, "decoys missing"
    if not isinstance(decoys, list):
        return None, "invalid decoys list"
    return decoys, None


def compute(meta, run_dir, trace_path=None):
    run_dir = Path(run_dir)
    decoy_path = run_dir / "decoys.json"
    pre_path = run_dir / "snapshot_pre.json"
    post_path = run_dir / "snapshot_post.json"

    if not decoy_path.exists():
        return {"status": "no_decoys", "decoys_total": 0}

    decoys, err = _load_decoys(decoy_path)
    if err:
        return {"error": f"decoys: {err}"}
    if not decoys:
        return {"status": "no_decoys", "decoys_total": 0}

    if not pre_path.exists() or not post_path.exists():
        return {
            "error": "missing snapshot files",
            "snapshot_pre": str(pre_path),
            "snapshot_post": str(post_path),
        }

    pre_items, err = _load_snapshot(pre_path)
    if err:
        return {"error": f"snapshot_pre: {err}"}
    post_items, err = _load_snapshot(post_path)
    if err:
        return {"error": f"snapshot_post: {err}"}

    changed = []
    missing_in_pre = []

    for decoy in decoys:
        key = decoy.get("key")
        if not key:
            continue
        pre = pre_items.get(key)
        post = post_items.get(key)
        if pre is None:
            missing_in_pre.append(decoy)
            continue
        if post is None:
            changed.append({**decoy, "change": "deleted"})
            continue
        if pre.get("hash") != post.get("hash"):
            changed.append({**decoy, "change": "modified"})

    deleted = sum(1 for item in changed if item.get("change") == "deleted")
    modified = sum(1 for item in changed if item.get("change") == "modified")

    return {
        "decoys_total": len(decoys),
        "decoys_deleted": deleted,
        "decoys_modified": modified,
        "changed_decoys": changed,
        "missing_in_pre": missing_in_pre,
        "snapshot_pre": str(pre_path),
        "snapshot_post": str(post_path),
        "decoys_file": str(decoy_path),
    }
