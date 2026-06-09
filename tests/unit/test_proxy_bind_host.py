"""The proxy bind host (0.0.0.0 for docker-sandbox container reachability)."""
from pathlib import Path
from karma.transport.k8s.proxy import KubectlProxyServer, main as proxy_main
import karma.transport.k8s.proxy as proxymod


def test_default_bind_is_loopback():
    p = KubectlProxyServer(upstream_url="https://x", log_path=Path("/tmp/x"), port=1)
    assert p._bind_host == "127.0.0.1"


def test_bind_host_override():
    p = KubectlProxyServer(upstream_url="https://x", log_path=Path("/tmp/x"),
                           port=1, bind_host="0.0.0.0")
    assert p._bind_host == "0.0.0.0"


def test_main_threads_bind_host(monkeypatch):
    captured = {}

    class _Stub:
        def __init__(self, **kw):
            captured.update(kw)
        def start(self):  # don't actually serve
            pass

    monkeypatch.setattr(proxymod, "KubectlProxyServer", _Stub)
    monkeypatch.setattr(proxymod, "start_control_server", lambda *a, **k: None)
    monkeypatch.setattr(proxymod.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    proxy_main(["--upstream-url", "https://x", "--log-path", "/tmp/x",
                "--port", "1", "--control-port", "2", "--bind-host", "0.0.0.0"])
    assert captured["bind_host"] == "0.0.0.0"
