"""Unit tests for karma.interfaces.http.events.EventHub."""

from karma.interfaces.http.events import EventHub


class TestEventHub:
    def test_live_subscriber_receives_published_events(self):
        h = EventHub()
        sub = h.subscribe("r1")
        h.publish("r1", {"type": "stage_complete", "n": 1})
        assert sub.get_nowait() == {"type": "stage_complete", "n": 1}

    def test_late_subscriber_replays_buffered_history(self):
        h = EventHub()
        h.publish("r1", {"n": 1})
        h.publish("r1", {"n": 2})
        sub = h.subscribe("r1")  # subscribes AFTER events were published
        assert sub.get_nowait() == {"n": 1}
        assert sub.get_nowait() == {"n": 2}

    def test_close_pushes_sentinel_to_subscribers(self):
        h = EventHub()
        sub = h.subscribe("r1")
        h.close("r1")
        assert sub.get_nowait() is None

    def test_subscribe_after_close_gets_sentinel(self):
        h = EventHub()
        h.publish("r1", {"n": 1})
        h.close("r1")
        sub = h.subscribe("r1")
        assert sub.get_nowait() == {"n": 1}
        assert sub.get_nowait() is None

    def test_buffer_is_bounded(self):
        h = EventHub(buffer_size=3)
        for i in range(10):
            h.publish("r1", {"n": i})
        sub = h.subscribe("r1")
        drained = [sub.get_nowait() for _ in range(3)]
        assert drained == [{"n": 7}, {"n": 8}, {"n": 9}]

    def test_multiple_subscribers_each_receive(self):
        h = EventHub()
        a = h.subscribe("r1")
        b = h.subscribe("r1")
        h.publish("r1", {"n": 1})
        assert a.get_nowait() == {"n": 1}
        assert b.get_nowait() == {"n": 1}

    def test_is_known_and_forget(self):
        h = EventHub()
        assert h.is_known("r1") is False
        h.publish("r1", {"n": 1})
        assert h.is_known("r1") is True
        h.forget("r1")
        assert h.is_known("r1") is False

    def test_unsubscribe_stops_delivery(self):
        h = EventHub()
        sub = h.subscribe("r1")
        h.unsubscribe("r1", sub)
        h.publish("r1", {"n": 1})
        # buffered but not delivered to the removed queue
        assert sub.empty()

    def test_publish_after_close_is_ignored(self):
        # Regression: an event published after close() must not land after the
        # terminal sentinel (e.g. a cancel racing a run_complete publish).
        h = EventHub()
        sub = h.subscribe("r1")
        h.publish("r1", {"n": 1})
        h.close("r1")
        h.publish("r1", {"type": "run_complete"})  # racing producer
        seen = []
        while True:
            x = sub.get_nowait()
            seen.append(x)
            if x is None:
                break
        assert seen == [{"n": 1}, None]

    def test_close_is_idempotent(self):
        h = EventHub()
        sub = h.subscribe("r1")
        h.close("r1")
        h.close("r1")  # second close must not push a second sentinel
        assert sub.get_nowait() is None
        assert sub.empty()

    def test_evicts_oldest_finished_streams_past_cap(self):
        from karma.interfaces.http import events as ev
        h = EventHub()
        # Create and finish more streams than the cap; finished ones evict.
        for i in range(ev._MAX_STREAMS + 10):
            sid = f"s{i}"
            h.publish(sid, {"n": i})
            h.close(sid)
        # total retained streams stays within the cap
        assert len(h._buffers) <= ev._MAX_STREAMS
        # the oldest are gone, the newest remain
        assert not h.is_known("s0")
        assert h.is_known(f"s{ev._MAX_STREAMS + 9}")
