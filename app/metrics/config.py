from pathlib import Path

import yaml

from ..settings import RESOURCES_DIR


_CONFIG_CACHE = None


def _load_config():
    path = RESOURCES_DIR / "metrics.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def get_metric_config(name):
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = _load_config()
    config = _CONFIG_CACHE.get(name, {})
    if not isinstance(config, dict):
        return {}
    return config
