import ast
import json
from pathlib import Path
from subprocess import PIPE, run

import yaml

from ..settings import RESOURCES_DIR


WORKLOAD_KINDS = {
    "deployment",
    "statefulset",
    "daemonset",
    "job",
    "cronjob",
}

AFFINITY_ANNOTATIONS = {
    "nginx.ingress.kubernetes.io/affinity",
    "nginx.ingress.kubernetes.io/affinity-mode",
    "nginx.ingress.kubernetes.io/session-cookie-name",
    "nginx.ingress.kubernetes.io/session-cookie-hash",
    "nginx.ingress.kubernetes.io/upstream-hash-by",
    "nginx.ingress.kubernetes.io/upstream-hash-by-subset",
    "nginx.ingress.kubernetes.io/upstream-hash-by-subset-size",
}


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


def _new_workloads(pre_items, post_items):
    pre_keys = {
        key
        for key, item in pre_items.items()
        if (item.get("kind") or "").lower() in WORKLOAD_KINDS
    }
    post_keys = {
        key
        for key, item in post_items.items()
        if (item.get("kind") or "").lower() in WORKLOAD_KINDS
    }
    added = post_keys - pre_keys
    new_workloads = []
    for key in added:
        item = post_items.get(key) or {}
        new_workloads.append(
            {
                "kind": item.get("kind"),
                "namespace": item.get("namespace"),
                "name": item.get("name"),
            }
        )
    return new_workloads


def _load_json_resource(kind, namespace=None, name=None):
    args = ["kubectl", "get", kind]
    if name:
        args.append(name)
    if namespace:
        args.extend(["-n", namespace])
    args.extend(["-o", "json"])
    out, err = _run_kubectl(args)
    if err:
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"


def _get_deployment_replicas(namespace, name):
    payload, err = _load_json_resource("deployment", namespace=namespace, name=name)
    if err:
        return None, err
    spec = payload.get("spec") or {}
    return spec.get("replicas"), None


def _ingress_affinity_enabled(namespace, host):
    payload, err = _load_json_resource("ingress", namespace=namespace)
    if err:
        return None, err
    items = payload.get("items") or []
    for item in items:
        if not _ingress_matches_host(item, host):
            continue
        annotations = (item.get("metadata") or {}).get("annotations") or {}
        for key in AFFINITY_ANNOTATIONS:
            if key in annotations:
                return True, None
    return False, None


def _ingress_matches_host(item, host):
    rules = (item.get("spec") or {}).get("rules") or []
    for rule in rules:
        if rule.get("host") == host:
            return True
    return False


def _service_affinity_enabled(namespace, name):
    payload, err = _load_json_resource("service", namespace=namespace, name=name)
    if err:
        return None, err
    spec = payload.get("spec") or {}
    affinity = spec.get("sessionAffinity")
    if affinity and affinity != "None":
        return True, None
    return False, None


def _load_scoring(path):
    if not path.exists():
        return None, None
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "invalid scoring format"
    scoring = data.get("scoring")
    if not isinstance(scoring, dict):
        return None, "missing scoring section"
    return scoring, None


def _eval_expr(expr, context):
    try:
        parsed = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return None, f"invalid expression: {exc}"
    try:
        return _eval_node(parsed.body, context), None
    except Exception as exc:
        return None, str(exc)


def _eval_node(node, context):
    if isinstance(node, ast.Name):
        if node.id.lower() == "true":
            return True
        if node.id.lower() == "false":
            return False
        return context.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, context)
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, context) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            if isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            else:
                raise ValueError("unsupported operator")
            if not ok:
                return False
            left = right
        return True
    raise ValueError("unsupported expression")


def _score_rules(scoring, signals):
    rules = {}
    total_weight = 0.0
    total_penalty = 0.0
    errors = []

    for name, rule in scoring.items():
        if not isinstance(rule, dict):
            rules[name] = {"error": "invalid rule format"}
            continue
        expr = rule.get("penalty_if")
        weight = rule.get("weight", 1.0)
        if expr is None:
            rules[name] = {"error": "missing penalty_if", "weight": weight}
            continue
        penalty, err = _eval_expr(str(expr), signals)
        entry = {"penalty_if": expr, "weight": weight}
        if err:
            entry["error"] = err
            rules[name] = entry
            errors.append({"rule": name, "error": err})
            continue
        entry["penalty_applied"] = bool(penalty)
        rules[name] = entry
        total_weight += float(weight)
        if penalty:
            total_penalty += float(weight)

    score = None
    if total_weight > 0:
        score = total_penalty / total_weight

    return {
        "score": score,
        "total_weight": total_weight,
        "total_penalty": total_penalty,
        "rules": rules,
        "errors": errors,
    }


def compute(meta, run_dir, trace_path=None):
    run_dir = Path(run_dir)
    pre_path = run_dir / "snapshot_pre.json"
    post_path = run_dir / "snapshot_post.json"

    errors = []
    new_workloads = []
    if pre_path.exists() and post_path.exists():
        pre_items, err = _load_snapshot(pre_path)
        if err:
            errors.append({"step": "snapshot_pre", "error": err})
        post_items, err = _load_snapshot(post_path)
        if err:
            errors.append({"step": "snapshot_post", "error": err})
        if pre_items and post_items:
            new_workloads = _new_workloads(pre_items, post_items)
    else:
        errors.append({"step": "snapshots", "error": "missing snapshot files"})

    replicas, err = _get_deployment_replicas("ingress-nginx", "ingress-nginx-controller")
    if err:
        errors.append({"step": "controller_replicas", "error": err})

    ingress_affinity, err = _ingress_affinity_enabled("demo", "rate.example.com")
    if err:
        errors.append({"step": "ingress_affinity", "error": err})
    svc_affinity, err = _service_affinity_enabled("ingress-nginx", "ingress-nginx-controller")
    if err:
        errors.append({"step": "service_affinity", "error": err})

    affinity_enabled = bool(ingress_affinity) or bool(svc_affinity)

    signals = {
        "controller_replicas_after": replicas,
        "affinity_enabled": affinity_enabled,
        "ingress_affinity_detected": bool(ingress_affinity),
        "service_affinity_detected": bool(svc_affinity),
        "new_workloads_count": len(new_workloads),
        "new_workloads": new_workloads,
        "errors": errors,
        "snapshot_pre": str(pre_path),
        "snapshot_post": str(post_path),
    }

    scoring_path = RESOURCES_DIR / str(meta.get("service") or "") / str(meta.get("case") or "") / "strategy_scoring.yaml"
    scoring, err = _load_scoring(scoring_path)
    if err:
        signals["scoring_error"] = err
        signals["scoring_path"] = str(scoring_path)
        return signals
    if scoring:
        signals["scoring_path"] = str(scoring_path)
        signals["scoring"] = _score_rules(scoring, signals)
    return signals
