import re
from unittest.mock import patch

from app.runner import BenchmarkApp

LABEL_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_.]*[A-Za-z0-9]$")


def _make_app():
    # Unit tests should not require cluster access.
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_label_value_truncation_invalid():
    app = _make_app()
    value = "2026-02-09T03-16-47Z_rabbitmq-experiments_blue_green_migration_"
    out = app._sanitize_label_value(value)
    assert LABEL_RE.match(out), out
    assert not out.endswith("_")


def test_label_value_hash_fallback():
    app = _make_app()
    value = "***invalid$$$" * 20
    out = app._sanitize_label_value(value)
    assert LABEL_RE.match(out), out
    assert len(out) <= 63


def test_label_value_short_ok():
    app = _make_app()
    value = "networkpolicy_block"
    out = app._sanitize_label_value(value)
    assert out == value
