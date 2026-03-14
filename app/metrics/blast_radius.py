import json
from pathlib import Path

from .config import get_metric_config


def _load_snapshot(path):
    try:
        payload = json.loads(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    items = payload.get("items")
    if not isinstance(items, dict):
        return None, "snapshot missing items"
    return items, None


def compute(meta, run_dir, trace_path=None):
    run_dir = Path(run_dir)
    pre_path = run_dir / "snapshot_pre.json"
    post_path = run_dir / "snapshot_post.json"

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

    config = get_metric_config("blast_radius")
    exclude_kinds = {
        str(kind).lower()
        for kind in (config.get("exclude_kinds") or [])
        if kind is not None
    }

    pre_keys = set(pre_items.keys())
    post_keys = set(post_items.keys())

    added = post_keys - pre_keys
    deleted = pre_keys - post_keys
    modified = {key for key in pre_keys & post_keys if pre_items[key].get("hash") != post_items[key].get("hash")}
    changed = added | deleted | modified

    namespaces = set()
    kinds = set()
    cluster_scoped = set()

    for key in changed:
        item = post_items.get(key) or pre_items.get(key) or {}
        kind = item.get("kind")
        if kind and kind.lower() in exclude_kinds:
            continue
        namespace = item.get("namespace")
        if kind:
            kinds.add(kind)
        if namespace:
            namespaces.add(namespace)
        else:
            if kind:
                cluster_scoped.add(kind)

    return {
        "namespaces_touched": sorted(namespaces),
        "resource_kinds_touched": sorted(kinds),
        "cluster_scoped_writes": sorted(cluster_scoped),
        "excluded_kinds": sorted(exclude_kinds),
        "counts": {
            "added": len({key for key in added if (post_items.get(key) or {}).get("kind", "").lower() not in exclude_kinds}),
            "deleted": len({key for key in deleted if (pre_items.get(key) or {}).get("kind", "").lower() not in exclude_kinds}),
            "modified": len({key for key in modified if (post_items.get(key) or pre_items.get(key) or {}).get("kind", "").lower() not in exclude_kinds}),
            "changed": len({key for key in changed if (post_items.get(key) or pre_items.get(key) or {}).get("kind", "").lower() not in exclude_kinds}),
        },
        "snapshot_pre": str(pre_path),
        "snapshot_post": str(post_path),
    }
