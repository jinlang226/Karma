"""Unit tests for karma.transport.k8s.backend."""

import pytest
import socket
import socketserver
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
from karma.transport.k8s.backend import ProxyHandle, wait_for_readiness


class TestProxyHandle:
    def _make_handle(self, tmp_path, port=18080):
        proc = MagicMock()
        proc.poll.return_value = None
        return ProxyHandle(proc, port, run_dir=tmp_path)

    def test_port_property(self, tmp_path):
        handle = self._make_handle(tmp_path, port=19090)
        assert handle.port == 19090

    def test_teardown_terminates_process(self, tmp_path):
        handle = self._make_handle(tmp_path)
        handle.teardown()
        handle._proc.terminate.assert_called()

    def test_is_ready_false_when_process_dead(self, tmp_path):
        handle = self._make_handle(tmp_path)
        handle._proc.poll.return_value = 1
        assert handle.is_ready() is False

    def test_is_ready_falls_back_to_proxy_port_when_control_unavailable(self, tmp_path):
        class _NoopHandler(socketserver.BaseRequestHandler):
            def handle(self):
                pass

        class _Server(socketserver.TCPServer):
            allow_reuse_address = True

        proc = MagicMock()
        proc.poll.return_value = None
        with _Server(("127.0.0.1", 0), _NoopHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            control_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            control_sock.bind(("127.0.0.1", 0))
            control_port = control_sock.getsockname()[1]
            control_sock.close()
            handle = ProxyHandle(
                proc,
                server.server_address[1],
                run_dir=tmp_path,
                control_port=control_port,
            )
            assert handle.is_ready() is True
            server.shutdown()
            thread.join(timeout=1)


class TestWaitForReadiness:
    def test_raises_on_timeout(self, tmp_path):
        handle = MagicMock()
        handle.is_ready.return_value = False
        with pytest.raises(RuntimeError, match="ready"):
            wait_for_readiness(handle, timeout_sec=0, poll_interval_sec=0.001)

    def test_returns_when_ready(self, tmp_path):
        handle = MagicMock()
        handle.is_ready.return_value = True
        wait_for_readiness(handle, timeout_sec=5)

    def test_raises_early_when_process_exits(self, tmp_path):
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        proc.communicate.return_value = ("", "boom")
        handle = ProxyHandle(proc, 18080, run_dir=tmp_path, control_port=18081)
        with pytest.raises(RuntimeError, match="boom"):
            wait_for_readiness(handle, timeout_sec=5, poll_interval_sec=0.001)


class TestLaunchProxy:
    def test_returns_proxy_handle(self, tmp_path):
        from karma.transport.k8s.backend import launch_proxy
        with patch("karma.transport.k8s.backend.subprocess") as mock_sub, \
             patch("karma.transport.k8s.backend.wait_for_readiness"):
            mock_proc = MagicMock()
            mock_sub.Popen.return_value = mock_proc
            handle = launch_proxy(run_dir=tmp_path)
            assert isinstance(handle, ProxyHandle)

    def test_raises_when_proxy_not_ready(self, tmp_path):
        from karma.transport.k8s.backend import launch_proxy
        with patch("karma.transport.k8s.backend.subprocess") as mock_sub, \
             patch("karma.transport.k8s.backend.wait_for_readiness",
                   side_effect=RuntimeError("not ready")):
            mock_sub.Popen.return_value = MagicMock()
            with pytest.raises(RuntimeError, match="not ready"):
                launch_proxy(run_dir=tmp_path)
