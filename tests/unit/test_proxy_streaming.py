"""Watch/follow detection + upgrade status parsing for the proxy."""
from karma.transport.k8s.proxy import _is_streaming_request, _read_upstream_status


def test_watch_true_is_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods?watch=true") is True
    assert _is_streaming_request("/api/v1/pods?labelSelector=a%3Db&watch=1") is True


def test_follow_is_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods/p/log?follow=true") is True


def test_non_watch_is_not_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods") is False
    assert _is_streaming_request("/api/v1/pods?watch=false") is False
    assert _is_streaming_request("/version") is False


class _FakeSock:
    """Socket stub that yields preset byte chunks from successive recv() calls."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, _n):
        return self._chunks.pop(0) if self._chunks else b""


def test_read_upstream_status_switch():
    # A successful protocol switch: the real 101 is parsed and every peeked
    # byte (status line + headers) is returned for forwarding to the client.
    sock = _FakeSock([b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: SPDY/3.1\r\n\r\n"])
    code, head = _read_upstream_status(sock)
    assert code == 101
    assert head.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")


def test_read_upstream_status_rejected():
    # A rejected exec (RBAC/404): the log must record the real 4xx, not 101.
    assert _read_upstream_status(_FakeSock([b"HTTP/1.1 403 Forbidden\r\n\r\nno"]))[0] == 403
    assert _read_upstream_status(_FakeSock([b"HTTP/1.1 404 Not Found\r\n\r\n"]))[0] == 404


def test_read_upstream_status_line_split_across_recvs():
    # The status line can arrive in pieces; the reader accumulates until CRLF.
    sock = _FakeSock([b"HTTP/1.1 ", b"500 Internal", b" Server Error\r\n\r\n"])
    code, head = _read_upstream_status(sock)
    assert code == 500
    assert head == b"HTTP/1.1 500 Internal Server Error\r\n\r\n"


def test_read_upstream_status_unparseable_falls_back_to_none():
    # Garbage or an empty stream -> None, so the caller keeps the 101 default.
    assert _read_upstream_status(_FakeSock([b"garbage line\r\n"]))[0] is None
    assert _read_upstream_status(_FakeSock([]))[0] is None
