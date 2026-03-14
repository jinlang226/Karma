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


def _load_yaml(path):
    if not path.exists():
        return None, "config file not found"
    try:
        data = yaml.safe_load(path.read_text())
    except Exception as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "invalid config format"
    return data, None


def _truthy(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _load_configmap(namespace, name):
    out, err = _run_kubectl(["kubectl", "-n", namespace, "get", "configmap", name, "-o", "json"])
    if err:
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"


def _load_ingresses(namespace):
    out, err = _run_kubectl(["kubectl", "-n", namespace, "get", "ingress", "-o", "json"])
    if err:
        return [], err
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return [], f"json decode failed: {exc}"
    return payload.get("items") or [], None


def _ingress_key(item):
    meta = item.get("metadata") or {}
    return meta.get("namespace"), meta.get("name")


def compute(meta, run_dir, trace_path=None):
    service = meta.get("service")
    case = meta.get("case")
    config_path = RESOURCES_DIR / str(service or "") / str(case or "") / "otel_scope_guardrails.yaml"
    config, err = _load_yaml(config_path)
    if err:
        return {"error": err, "config_path": str(config_path)}

    target = config.get("target_ingress") or {}
    target_namespace = target.get("namespace")
    target_name = target.get("name")
    scope = config.get("scope") or {}
    namespaces = scope.get("namespaces") or []

    errors = []
    configmap = config.get("configmap") or {}
    cm_namespace = configmap.get("namespace", "ingress-nginx")
    cm_name = configmap.get("name", "ingress-nginx-controller")

    cm, cm_err = _load_configmap(cm_namespace, cm_name)
    if cm_err:
        errors.append({"step": "configmap", "error": cm_err})
    cm_data = (cm or {}).get("data") or {}
    global_value = cm_data.get("enable-opentelemetry")
    global_enabled = _truthy(global_value)

    ingresses = []
    ingress_errors = []
    for ns in namespaces:
        items, err = _load_ingresses(ns)
        if err:
            ingress_errors.append({"namespace": ns, "error": err})
            continue
        ingresses.extend(items)
    errors.extend(ingress_errors)

    target_annotation = None
    target_enabled = False
    other_enabled = []
    other_missing = []
    other_disabled = []

    for item in ingresses:
        ns, name = _ingress_key(item)
        annotations = (item.get("metadata") or {}).get("annotations") or {}
        value = annotations.get("nginx.ingress.kubernetes.io/enable-opentelemetry")
        is_target = (ns == target_namespace and name == target_name)
        if is_target:
            target_annotation = value
            target_enabled = _truthy(value)
            continue
        if value is None:
            other_missing.append({"namespace": ns, "name": name})
        elif _truthy(value):
            other_enabled.append({"namespace": ns, "name": name})
        else:
            other_disabled.append({"namespace": ns, "name": name})

    return {
        "otel_global_enabled": global_enabled,
        "otel_global_value": global_value,
        "configmap": {"namespace": cm_namespace, "name": cm_name},
        "target_ingress": {"namespace": target_namespace, "name": target_name},
        "target_annotation": target_annotation,
        "target_enabled": target_enabled,
        "other_ingress_enabled_count": len(other_enabled),
        "other_ingress_missing_annotation_count": len(other_missing),
        "other_ingress_disabled_count": len(other_disabled),
        "other_ingress_enabled": other_enabled,
        "other_ingress_missing_annotation": other_missing,
        "other_ingress_disabled": other_disabled,
        "errors": errors,
        "config_path": str(config_path),
    }
