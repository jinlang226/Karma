"""Watch/follow detection for proxy streaming."""
from karma.transport.k8s.proxy import _is_streaming_request


def test_watch_true_is_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods?watch=true") is True
    assert _is_streaming_request("/api/v1/pods?labelSelector=a%3Db&watch=1") is True


def test_follow_is_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods/p/log?follow=true") is True


def test_non_watch_is_not_streaming():
    assert _is_streaming_request("/api/v1/namespaces/x/pods") is False
    assert _is_streaming_request("/api/v1/pods?watch=false") is False
    assert _is_streaming_request("/version") is False
