import json
from pathlib import Path

import yaml


def list_decoy_files(case_dir):
    decoy_dir = Path(case_dir) / "decoy"
    if not decoy_dir.is_dir():
        return []
    files = []
    for path in sorted(decoy_dir.iterdir()):
        if path.is_file() and path.suffix in (".yml", ".yaml"):
            files.append(path)
    return files


def _decoy_key(kind, namespace, name):
    return f"{kind}|{namespace or '_cluster'}|{name}"


def load_decoys(paths):
    decoys = []
    for path in paths:
        try:
            docs = yaml.safe_load_all(path.read_text())
        except Exception:
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind")
            meta = doc.get("metadata") or {}
            name = meta.get("name")
            namespace = meta.get("namespace")
            if not kind or not name:
                continue
            decoys.append(
                {
                    "kind": kind,
                    "namespace": namespace,
                    "name": name,
                    "key": _decoy_key(kind, namespace, name),
                    "path": str(path),
                }
            )
    return decoys


def write_decoys_file(run_dir, decoys):
    run_dir = Path(run_dir)
    payload = {"decoys": decoys}
    path = run_dir / "decoys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def build_decoy_commands(paths, action):
    commands = []
    for path in paths:
        commands.append({"command": ["kubectl", action, "-f", str(path)], "sleep": 0})
    return commands
