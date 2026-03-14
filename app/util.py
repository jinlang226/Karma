import base64
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .settings import NAME_PATTERN


def utc_now():
    return datetime.now(timezone.utc)


def ts_str(dt=None):
    if dt is None:
        dt = utc_now()
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


def ts_epoch(dt=None):
    if dt is None:
        dt = utc_now()
    return int(dt.timestamp())


def parse_ts(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    if isinstance(value, str):
        if value.isdigit():
            return datetime.fromtimestamp(int(value), timezone.utc)
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H_%M_%SZ",
            "%Y-%m-%dT%H-%M-%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                continue
        try:
            iso_value = value
            if iso_value.endswith("Z"):
                iso_value = iso_value[:-1] + "+00:00"
            parsed = datetime.fromisoformat(iso_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None
    return None


def safe_join(cmd_list):
    try:
        return shlex.join(cmd_list)
    except Exception:
        return " ".join(str(p) for p in cmd_list)


def encode_case_id(service, case, test_file):
    raw = f"{service}|{case}|{test_file}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def decode_case_id(case_id):
    padding = "=" * ((4 - len(case_id) % 4) % 4)
    raw = base64.urlsafe_b64decode(case_id + padding).decode("utf-8")
    parts = raw.split("|")
    if len(parts) != 3:
        raise ValueError("Invalid case id")
    return parts[0], parts[1], parts[2]


def sanitize_name(value):
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in value)


def is_valid_name(value):
    return bool(NAME_PATTERN.match(value))


def read_yaml(path):
    try:
        return yaml.safe_load(Path(path).read_text())
    except Exception as exc:
        return {"_error": str(exc)}


def normalize_commands(cmds):
    if not cmds:
        return []
    normalized = []
    for item in cmds:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if isinstance(command, list):
            command = [str(part) for part in command]
        elif command is not None:
            command = str(command)
        sleep = item.get("sleep", 0)
        timeout_sec = item.get("timeout_sec")
        if timeout_sec is None:
            timeout_sec = item.get("timeoutSec")
        namespace_role = item.get("namespace_role")
        if namespace_role is None:
            namespace_role = item.get("namespaceRole")
        row = {"command": command, "sleep": sleep, "timeout_sec": timeout_sec}
        if namespace_role is not None:
            row["namespace_role"] = str(namespace_role)
        normalized.append(row)
    return normalized


def normalize_metrics(metrics):
    if not metrics:
        return []
    if isinstance(metrics, list):
        return [item for item in metrics if isinstance(item, str) and item]
    return []




def command_to_string(command):
    if command is None:
        return ""
    if isinstance(command, list):
        return safe_join([str(part) for part in command])
    return str(command)


def list_requires_shell(command_list):
    shell_tokens = {"|", "||", "&&", ";", ">", ">>", "<"}
    return any(token in shell_tokens for token in command_list)


_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smh])?$")


def parse_duration_seconds(value):
    """
    Parse a simple duration string into seconds.

    Supported forms:
    - "120" (seconds)
    - "120s"
    - "5m"
    - "2h"
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = int(value)
        except Exception:
            return None
        return seconds if seconds >= 0 else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    try:
        raw = int(match.group("value"))
    except Exception:
        return None
    unit = match.group("unit") or "s"
    mult = {"s": 1, "m": 60, "h": 3600}.get(unit)
    if mult is None:
        return None
    seconds = raw * mult
    return seconds if seconds >= 0 else None


_TIMEOUT_FLAG_RE = re.compile(
    r"--(?P<flag>timeout|request-timeout)(?:=|\s+)(?P<value>\d+[smh]?)",
    flags=re.IGNORECASE,
)


def infer_command_timeout_seconds(command):
    """
    Infer an execution timeout (in seconds) from a command by scanning for
    `--timeout` and `--request-timeout` flags.

    Returns the maximum parsed timeout if any are found, otherwise None.
    """
    text = command_to_string(command)
    if not text:
        return None
    timeouts = []
    for match in _TIMEOUT_FLAG_RE.finditer(text):
        seconds = parse_duration_seconds(match.group("value"))
        if seconds is None:
            continue
        # 0 means "no timeout" for some kubectl flags; ignore it for inference.
        if seconds <= 0:
            continue
        timeouts.append(seconds)
    return max(timeouts) if timeouts else None
