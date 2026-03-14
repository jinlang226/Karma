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
    try:
        data = yaml.safe_load(Path(path).read_text())
    except Exception as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "invalid guardrails format"
    return data, None


def _load_deployment(namespace, name):
    out, err = _run_kubectl(["kubectl", "-n", namespace, "get", "deployment", name, "-o", "json"])
    if err:
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"


def _find_container(spec, container_name=None):
    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers") or []
    if not containers:
        return None
    if container_name:
        for container in containers:
            if container.get("name") == container_name:
                return container
    return containers[0]


def _args_list(container):
    args = container.get("args") or []
    return [str(item) for item in args if item is not None]


def _load_ingressclasses():
    out, err = _run_kubectl(["kubectl", "get", "ingressclass", "-o", "json"])
    if err:
        return [], err
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return [], f"json decode failed: {exc}"
    return payload.get("items") or [], None


def _truthy(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def compute(meta, run_dir, trace_path=None):
    service = meta.get("service")
    case = meta.get("case")
    guardrails_path = RESOURCES_DIR / str(service or "") / str(case or "") / "guardrails.yaml"
    guardrails, err = _load_yaml(guardrails_path)
    if err:
        return {"error": err, "guardrails_path": str(guardrails_path)}

    controller_rules = guardrails.get("controller_args") or []
    ingress_constraints = guardrails.get("ingressclass_constraints") or []
    forbid_any_default = bool(guardrails.get("forbid_any_default_class"))

    controller_violations = []
    controller_errors = []

    for rule in controller_rules:
        if not isinstance(rule, dict):
            continue
        namespace = rule.get("namespace")
        name = rule.get("name")
        if not namespace or not name:
            continue
        container_name = rule.get("container")
        must_include = [str(item) for item in (rule.get("must_include") or []) if item]
        forbidden = [str(item) for item in (rule.get("forbidden") or []) if item]

        deployment, dep_err = _load_deployment(namespace, name)
        if dep_err:
            controller_errors.append({"namespace": namespace, "name": name, "error": dep_err})
            continue

        container = _find_container(deployment.get("spec") or {}, container_name=container_name)
        if not container:
            controller_errors.append(
                {"namespace": namespace, "name": name, "error": "container not found"}
            )
            continue

        args = _args_list(container)
        missing = [item for item in must_include if item not in args]
        forbidden_found = [item for item in forbidden if item in args]
        if missing or forbidden_found:
            controller_violations.append(
                {
                    "namespace": namespace,
                    "name": name,
                    "missing": missing,
                    "forbidden": forbidden_found,
                }
            )

    ingress_items, ingress_err = _load_ingressclasses()
    ingress_errors = []
    if ingress_err:
        ingress_errors.append({"error": ingress_err})

    default_class_violations = []
    if ingress_items:
        if forbid_any_default:
            for item in ingress_items:
                annotations = (item.get("metadata") or {}).get("annotations") or {}
                if _truthy(annotations.get("ingressclass.kubernetes.io/is-default-class")):
                    default_class_violations.append(
                        {
                            "name": (item.get("metadata") or {}).get("name"),
                            "reason": "default_class_set",
                        }
                    )
        for constraint in ingress_constraints:
            if not isinstance(constraint, dict):
                continue
            name = constraint.get("name")
            if not name:
                continue
            forbidden_annotations = constraint.get("forbidden_annotations") or {}
            item = next(
                (item for item in ingress_items if (item.get("metadata") or {}).get("name") == name),
                None,
            )
            if not item:
                default_class_violations.append({"name": name, "reason": "missing"})
                continue
            annotations = (item.get("metadata") or {}).get("annotations") or {}
            for key, value in forbidden_annotations.items():
                current = annotations.get(key)
                if current is None:
                    continue
                if str(current) == str(value):
                    default_class_violations.append(
                        {"name": name, "reason": "forbidden_annotation", "key": key, "value": current}
                    )

    ok = not controller_violations and not controller_errors and not default_class_violations and not ingress_errors

    return {
        "ok": ok,
        "controller_violations": controller_violations,
        "controller_errors": controller_errors,
        "default_class_violations": default_class_violations,
        "ingress_errors": ingress_errors,
        "guardrails_path": str(guardrails_path),
    }
