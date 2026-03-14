import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from subprocess import PIPE, run


EXCLUDED_META_KEYS = {
    "creationTimestamp",
    "generation",
    "managedFields",
    "resourceVersion",
    "selfLink",
    "uid",
}


def _utc_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_kubectl(args):
    result = run(args, stdout=PIPE, stderr=PIPE, text=True)
    if result.returncode != 0:
        return None, result.stderr.strip() or "kubectl failed"
    return result.stdout, None


def _list_resources(namespaced):
    args = [
        "kubectl",
        "api-resources",
        "--verbs=list",
        f"--namespaced={'true' if namespaced else 'false'}",
        "-o",
        "name",
    ]
    out, err = _run_kubectl(args)
    if err:
        return [], err
    resources = [line.strip() for line in out.splitlines() if line.strip()]
    return resources, None


def _normalize_meta(meta):
    if not isinstance(meta, dict):
        return {}
    cleaned = dict(meta)
    for key in EXCLUDED_META_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _hash_object(obj):
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_item(item, list_kind):
    if not isinstance(item, dict):
        return None
    metadata = item.get("metadata") or {}
    name = metadata.get("name")
    if not name:
        return None
    namespace = metadata.get("namespace")
    kind = item.get("kind")
    if not kind and isinstance(list_kind, str) and list_kind.endswith("List"):
        kind = list_kind[: -len("List")]
    if not kind:
        kind = list_kind or "Unknown"
    uid = metadata.get("uid")

    normalized = dict(item)
    normalized.pop("status", None)
    normalized["metadata"] = _normalize_meta(metadata)
    obj_hash = _hash_object(normalized)

    key = f"{kind}|{namespace or '_cluster'}|{name}"
    return key, {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "uid": uid,
        "hash": obj_hash,
    }


def _collect_for_resource(resource, namespaced):
    args = ["kubectl", "get", resource]
    if namespaced:
        args.append("-A")
    args.extend(["-o", "json"])
    out, err = _run_kubectl(args)
    if err:
        return {}, err
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return {}, f"json decode failed: {exc}"
    items = payload.get("items") or []
    list_kind = payload.get("kind")
    records = {}
    for item in items:
        result = _record_item(item, list_kind)
        if not result:
            continue
        key, record = result
        records[key] = record
    return records, None


def capture_snapshot(output_path):
    output_path = Path(output_path)
    snapshot = {
        "generated_at": _utc_ts(),
        "items": {},
        "errors": [],
    }

    namespaced_resources, err = _list_resources(namespaced=True)
    if err:
        snapshot["errors"].append({"step": "list_namespaced", "error": err})
        namespaced_resources = []

    cluster_resources, err = _list_resources(namespaced=False)
    if err:
        snapshot["errors"].append({"step": "list_cluster", "error": err})
        cluster_resources = []

    for resource in namespaced_resources:
        records, err = _collect_for_resource(resource, namespaced=True)
        if err:
            snapshot["errors"].append({"resource": resource, "error": err})
            continue
        snapshot["items"].update(records)

    for resource in cluster_resources:
        records, err = _collect_for_resource(resource, namespaced=False)
        if err:
            snapshot["errors"].append({"resource": resource, "error": err})
            continue
        snapshot["items"].update(records)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2))
    return snapshot
