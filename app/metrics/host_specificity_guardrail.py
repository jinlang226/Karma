import json
from pathlib import Path
from subprocess import PIPE, run

import yaml

from ..settings import RESOURCES_DIR


def _run_kubectl(args):
    result = run(args, stdout=PIPE, stderr=PIPE, text=True)
    if result.returncode != 0:
        return None, result.stderr.strip() or "kubectl failed"
    return result.stdout, None


def _load_snapshot(path):
    try:
        payload = json.loads(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    items = payload.get("items")
    if not isinstance(items, dict):
        return None, "snapshot missing items"
    return items, None


def _load_yaml(path):
    if not path.exists():
        return None, "guardrails file not found"
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "invalid guardrails format"
    return data, None


def _is_wildcard(host):
    if host is None:
        return False
    return "*" in str(host)


def _load_ingress(namespace, name):
    args = ["kubectl", "get", "ingress", name, "-o", "json"]
    if namespace:
        args[1:1] = ["-n", namespace]
    out, err = _run_kubectl(args)
    if err:
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"


def _matches_scope(item, scope):
    namespace = scope.get("namespace")
    names = scope.get("names")
    if namespace and item.get("namespace") != namespace:
        return False
    if names and item.get("name") not in names:
        return False
    return True


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
    guardrails_path = RESOURCES_DIR / str(service or "") / str(case or "") / "host_guardrails.yaml"
    guardrails, err = _load_yaml(guardrails_path)
    if err:
        return {"error": err, "guardrails_path": str(guardrails_path)}

    scope = guardrails.get("scope") or {}
    rules = guardrails.get("rules") or {}
    forbid_empty = bool(rules.get("forbid_empty_host", True))
    forbid_wildcard = bool(rules.get("forbid_wildcard_host", True))

    pre_keys = set(pre_items.keys())
    post_keys = set(post_items.keys())
    added = post_keys - pre_keys
    modified = {key for key in pre_keys & post_keys if pre_items[key].get("hash") != post_items[key].get("hash")}
    changed = added | modified

    violations = []
    missing = []
    checked = 0

    for key in changed:
        item = post_items.get(key) or {}
        if (item.get("kind") or "").lower() != "ingress":
            continue
        info = {
            "namespace": item.get("namespace"),
            "name": item.get("name"),
        }
        if not _matches_scope(info, scope):
            continue

        ingress, err = _load_ingress(info.get("namespace"), info.get("name"))
        if err:
            missing.append({**info, "error": err})
            continue
        checked += 1

        rules_list = (ingress.get("spec") or {}).get("rules") or []
        if not rules_list and forbid_empty:
            violations.append({**info, "host": None, "reason": "empty_host"})
            continue

        for rule in rules_list:
            host = rule.get("host")
            if forbid_empty and (host is None or str(host).strip() == ""):
                violations.append({**info, "host": host, "reason": "empty_host"})
                continue
            if forbid_wildcard and _is_wildcard(host):
                violations.append({**info, "host": host, "reason": "wildcard_host"})

    ok = not violations and not missing
    return {
        "ok": ok,
        "checked_ingresses": checked,
        "violations": violations,
        "missing_ingresses": missing,
        "guardrails_path": str(guardrails_path),
        "snapshot_pre": str(pre_path),
        "snapshot_post": str(post_path),
    }
