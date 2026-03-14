from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from app.settings import ROOT


def normalize_control_url(control_url):
    if not control_url:
        return ""
    if "://" not in control_url:
        return f"http://{control_url}"
    return control_url


def control_listen_from_url(control_url, default_host="127.0.0.1", default_port=8082):
    normalized = normalize_control_url(control_url)
    parsed = urlparse(normalized)
    host = parsed.hostname or default_host
    port = parsed.port or default_port
    return f"{host}:{port}"


def is_local_host(host):
    return host in ("127.0.0.1", "localhost")


def read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json_file(path, payload):
    try:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def relative_path(path, root=ROOT):
    p = Path(path)
    if p.is_relative_to(root):
        return str(p.relative_to(root))
    return str(p)


def stable_json_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
