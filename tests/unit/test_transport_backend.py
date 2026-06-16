"""Unit tests for karma.transport.k8s.backend."""

import pytest
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
