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
