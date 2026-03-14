import base64
import json
from pathlib import Path
from subprocess import PIPE, run

import yaml

from ..settings import RESOURCES_DIR


PEM_MARKERS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
)

KEY_NAME_HINTS = {
    "tls.key",
    "ca.key",
    "test-ca.key",
    "test_ca.key",
}


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


def _run_kubectl(args):
    result = run(args, stdout=PIPE, stderr=PIPE, text=True)
    if result.returncode != 0:
        return None, result.stderr.strip() or "kubectl failed"
    return result.stdout, None


def _load_objects(kind):
    out, err = _run_kubectl(["kubectl", "get", kind, "-A", "-o", "json"])
    if err:
        return {}, err
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return {}, f"json decode failed: {exc}"
    objects = {}
    for item in payload.get("items") or []:
        meta = item.get("metadata") or {}
        name = meta.get("name")
        namespace = meta.get("namespace")
        if not name:
            continue
        objects[(namespace or ""), name] = item
    return objects, None


def _extract_ingress_tls_secrets():
    out, err = _run_kubectl(["kubectl", "get", "ingress", "-A", "-o", "json"])
    if err:
        return set(), err
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return set(), f"json decode failed: {exc}"
    secrets = set()
    for item in payload.get("items") or []:
        namespace = (item.get("metadata") or {}).get("namespace") or ""
        tls_list = (item.get("spec") or {}).get("tls") or []
        for tls in tls_list:
            if not isinstance(tls, dict):
                continue
            secret_name = tls.get("secretName")
            if secret_name:
                secrets.add((namespace, secret_name))
    return secrets, None


def _decode_value(value):
    if value is None:
        return ""
    try:
        raw = base64.b64decode(value)
    except Exception:
        return ""
    try:
        return raw.decode("utf-8", "ignore")
    except Exception:
        return ""


def _contains_pem(value):
    if not value:
        return False
    for marker in PEM_MARKERS:
        if marker in value:
            return True
    return False


def _scan_secret(secret):
    matches = []
    data = secret.get("data") or {}
    for key, value in data.items():
        if key in KEY_NAME_HINTS:
            matches.append({"match": "key_name", "field": key})
        decoded = _decode_value(value)
        if _contains_pem(decoded):
            matches.append({"match": "pem_marker", "field": key})
    return matches


def _scan_configmap(configmap):
    matches = []
    data = configmap.get("data") or {}
    for key, value in data.items():
        if key in KEY_NAME_HINTS:
            matches.append({"match": "key_name", "field": key})
        if _contains_pem(str(value)):
            matches.append({"match": "pem_marker", "field": key})
    return matches


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

    service = meta.get("service")
    case = meta.get("case")
    allowlist_path = RESOURCES_DIR / str(service or "") / str(case or "") / "leakage_allowlist.yaml"
    allowlist = _load_allowlist(allowlist_path)

    changed = []
    for key, post_item in post_items.items():
        kind = (post_item.get("kind") or "").lower()
        if kind not in {"secret", "configmap"}:
            continue
        pre_item = pre_items.get(key)
        if pre_item is None:
            change = "added"
        elif pre_item.get("hash") != post_item.get("hash"):
            change = "modified"
        else:
            continue
        changed.append(
            {
                "kind": post_item.get("kind"),
                "namespace": post_item.get("namespace"),
                "name": post_item.get("name"),
                "change": change,
            }
        )

    ingress_tls_secrets, ingress_err = _extract_ingress_tls_secrets()
    secret_map, secret_err = _load_objects("secret")
    config_map, config_err = _load_objects("configmap")

    errors = []
    if ingress_err:
        errors.append({"step": "list_ingress_tls", "error": ingress_err})
    if secret_err:
        errors.append({"step": "list_secrets", "error": secret_err})
    if config_err:
        errors.append({"step": "list_configmaps", "error": config_err})

    leaked = []
    ignored_ingress_tls = 0
    missing_objects = []

    for item in changed:
        if _is_allowlisted(item, allowlist):
            continue
        kind = (item.get("kind") or "").lower()
        namespace = item.get("namespace") or ""
        name = item.get("name")
        if not name:
            continue

        if kind == "secret":
            if (namespace, name) in ingress_tls_secrets:
                ignored_ingress_tls += 1
                continue
            secret = secret_map.get((namespace, name))
            if not secret:
                missing_objects.append({**item, "reason": "not_found"})
                continue
            matches = _scan_secret(secret)
        else:
            configmap = config_map.get((namespace, name))
            if not configmap:
                missing_objects.append({**item, "reason": "not_found"})
                continue
            matches = _scan_configmap(configmap)

        if matches:
            leaked.append({**item, "matches": matches})

    return {
        "leak_count": len(leaked),
        "checked_resources": len(changed),
        "ignored_ingress_tls": ignored_ingress_tls,
        "leaked_objects": leaked,
        "missing_objects": missing_objects,
        "errors": errors,
        "allowlist_path": str(allowlist_path),
        "snapshot_pre": str(pre_path),
        "snapshot_post": str(post_path),
    }
