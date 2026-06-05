"""
In-process event hub for Server-Sent Events.

The first cut of the HTTP layer stored one event ``queue.Queue`` per
request in a closure-local dict. That had two problems: a client that
connected to the stream *after* the run started missed every prior event,
and a reconnecting client got nothing because the queue was consumed once.
There was also no place for a heartbeat, so idle proxies dropped the
connection.

:class:`EventHub` fixes all three. Each run (or judge job) gets a bounded
ring buffer of past events plus a set of live subscriber queues. A new
subscriber is replayed the buffer first, then receives live events, so
late joiners and reconnects both see the full history. Completion is
signalled with a ``None`` sentinel pushed to every subscriber.

The hub is keyed by an opaque id, so the same instance carries both run
progress and judge-job progress -- the two event streams the old UI kept
separate now share one mechanism.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

_DEFAULT_BUFFER = 500
_SUBSCRIBER_MAX = 2000


class EventHub:
    """A buffered, multi-subscriber pub/sub channel keyed by stream id."""

    def __init__(self, buffer_size: int = _DEFAULT_BUFFER) -> None:
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._done: set[str] = set()
        self._buffer_size = buffer_size
        self._lock = threading.Lock()

    def publish(self, stream_id: str, event: dict[str, Any]) -> None:
        """Append *event* to the buffer and fan it out to live subscribers.

        The per-stream buffer is trimmed to ``buffer_size`` newest events.
        A subscriber whose queue is full silently drops the event rather
        than blocking the publisher.
        """
        with self._lock:
            buf = self._buffers.setdefault(stream_id, [])
            buf.append(event)
            overflow = len(buf) - self._buffer_size
            if overflow > 0:
                del buf[:overflow]
            subs = list(self._subscribers.get(stream_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def close(self, stream_id: str) -> None:
        """Mark *stream_id* finished and push the end sentinel to subscribers."""
        with self._lock:
            self._done.add(stream_id)
            subs = list(self._subscribers.get(stream_id, []))
        for q in subs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def subscribe(self, stream_id: str) -> queue.Queue:
        """Return a queue preloaded with buffered events and registered live.

        The returned queue first yields every buffered event (replay), then
        live events as they are published. If the stream already completed,
        the end sentinel is appended so the consumer terminates promptly.
        """
        q: queue.Queue = queue.Queue(maxsize=_SUBSCRIBER_MAX)
        with self._lock:
            for ev in self._buffers.get(stream_id, []):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    break
            self._subscribers.setdefault(stream_id, []).append(q)
            done = stream_id in self._done
        if done:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass
        return q

    def unsubscribe(self, stream_id: str, q: queue.Queue) -> None:
        """Remove *q* from the subscriber set for *stream_id*."""
        with self._lock:
            subs = self._subscribers.get(stream_id)
            if subs and q in subs:
                subs.remove(q)

    def is_known(self, stream_id: str) -> bool:
        """Return ``True`` when *stream_id* has any buffered or completed state."""
        with self._lock:
            return stream_id in self._buffers or stream_id in self._done

    def forget(self, stream_id: str) -> None:
        """Drop all buffered state for *stream_id* (used by cleanup)."""
        with self._lock:
            self._buffers.pop(stream_id, None)
            self._subscribers.pop(stream_id, None)
            self._done.discard(stream_id)


# Process-wide singleton shared by the run path and the judge-job path.
hub = EventHub()
