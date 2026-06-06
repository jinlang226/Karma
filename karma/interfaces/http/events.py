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
_MAX_STREAMS = 256


class EventHub:
    """A buffered, multi-subscriber pub/sub channel keyed by stream id.

    Buffers up to ``buffer_size`` recent events per stream and retains at
    most ``_MAX_STREAMS`` streams, evicting the oldest finished ones first
    so a long-lived server does not grow without bound. Once a stream is
    closed it is terminal: late events are ignored and ``close`` is
    idempotent, so a producer that races a ``close`` (e.g. cancellation)
    cannot strand an event after the end sentinel.
    """

    def __init__(self, buffer_size: int = _DEFAULT_BUFFER) -> None:
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._done: set[str] = set()
        self._buffer_size = buffer_size
        self._lock = threading.Lock()

    def _evict_locked(self) -> None:
        """Drop oldest finished streams (then oldest overall) past the cap.

        Caller must hold ``self._lock``. Dict insertion order makes the
        first keys the oldest. A stream with live subscribers is never
        evicted so an attached client is not cut off.
        """
        while len(self._buffers) > _MAX_STREAMS:
            victim = None
            for sid in self._buffers:
                if self._subscribers.get(sid):
                    continue
                victim = sid
                if sid in self._done:
                    break  # prefer a finished stream
            if victim is None:
                return  # everything left has live subscribers
            self._buffers.pop(victim, None)
            self._subscribers.pop(victim, None)
            self._done.discard(victim)

    def publish(self, stream_id: str, event: dict[str, Any]) -> None:
        """Append *event* to the buffer and fan it out to live subscribers.

        Ignored once the stream is closed, so nothing can land after the
        end sentinel. The per-stream buffer is trimmed to ``buffer_size``
        newest events; a subscriber whose queue is full drops the event
        rather than blocking the publisher.
        """
        with self._lock:
            if stream_id in self._done:
                return
            is_new = stream_id not in self._buffers
            buf = self._buffers.setdefault(stream_id, [])
            buf.append(event)
            overflow = len(buf) - self._buffer_size
            if overflow > 0:
                del buf[:overflow]
            if is_new:
                self._evict_locked()
            subs = list(self._subscribers.get(stream_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def close(self, stream_id: str) -> None:
        """Mark *stream_id* finished and push the end sentinel to subscribers.

        Idempotent: closing an already-closed stream does nothing, so a
        second ``close`` cannot push a second sentinel.
        """
        with self._lock:
            if stream_id in self._done:
                return
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
