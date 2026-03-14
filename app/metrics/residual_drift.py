import json
from pathlib import Path

import yaml

from ..settings import RESOURCES_DIR


def _load_snapshot(path):
    try:
        payload = json.loads(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    items = payload.get("items")
    if not isinstance(items, dict):
        return None, "snapshot missing items"
    return items, None


def _load_allowlist(path):
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    allowlist = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        name = item.get("name")
        namespace = item.get("namespace")
        if not kind or not name:
            continue
        allowlist.append(
            {
                "kind": str(kind).lower(),
                "name": str(name),
                "namespace": str(namespace) if namespace else None,
            }
        )
    return allowlist


def _is_allowlisted(entry, allowlist):
    kind = (entry.get("kind") or "").lower()
    name = entry.get("name")
    namespace = entry.get("namespace")
    for item in allowlist:
        if item.get("kind") != kind:
            continue
        if item.get("name") != name:
            continue
        if item.get("namespace") is None or item.get("namespace") == namespace:
            return True
    return False


def compute(meta, run_dir, trace_path=None):
    run_dir = Path(run_dir)
    base_path = run_dir / "snapshot_base.json"
    post_path = run_dir / "snapshot_post_cleanup.json"

    if not base_path.exists() or not post_path.exists():
        return {
            "error": "missing snapshot files",
            "snapshot_base": str(base_path),
            "snapshot_post_cleanup": str(post_path),
        }

    base_items, err = _load_snapshot(base_path)
    if err:
        return {"error": f"snapshot_base: {err}"}
    post_items, err = _load_snapshot(post_path)
    if err:
        return {"error": f"snapshot_post_cleanup: {err}"}

    service = meta.get("service")
    case = meta.get("case")
    allowlist_path = RESOURCES_DIR / str(service or "") / str(case or "") / "drift_allowlist.yaml"
    allowlist = _load_allowlist(allowlist_path)

    base_keys = set(base_items.keys())
    post_keys = set(post_items.keys())

    added = post_keys - base_keys
    modified = {key for key in base_keys & post_keys if base_items[key].get("hash") != post_items[key].get("hash")}

    drifted = []
    for key in added | modified:
        item = post_items.get(key) or base_items.get(key) or {}
        entry = {
            "kind": item.get("kind"),
            "namespace": item.get("namespace"),
            "name": item.get("name"),
            "change": "added" if key in added else "modified",
        }
        if _is_allowlisted(entry, allowlist):
            continue
        drifted.append(entry)

    return {
        "drift_count": len(drifted),
        "drifted_resources": drifted,
        "counts": {
            "added": sum(1 for item in drifted if item.get("change") == "added"),
            "modified": sum(1 for item in drifted if item.get("change") == "modified"),
        },
        "allowlist_path": str(allowlist_path),
        "snapshot_base": str(base_path),
        "snapshot_post_cleanup": str(post_path),
    }
