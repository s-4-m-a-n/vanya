from app.adapters.events import get_event_bus, subscribe


def test_handlers_receive_published_events():
    received = []

    @subscribe("observation.verified")
    def on_verified(event):
        received.append(event.payload)

    get_event_bus().publish("observation.verified", {"observation_id": "abc"})
    assert received == [{"observation_id": "abc"}]


def test_failing_handler_does_not_break_publisher_or_other_handlers():
    received = []

    @subscribe("alert.invasive")
    def broken(event):
        raise RuntimeError("boom")

    @subscribe("alert.invasive")
    def healthy(event):
        received.append(event.topic)

    event = get_event_bus().publish("alert.invasive", {})
    assert event.id
    assert received == ["alert.invasive"]


def test_async_bus_delivers_on_a_background_thread():
    import threading

    from app.adapters.events.inprocess import InProcessEventBus

    done = threading.Event()
    seen = {}

    @subscribe("test.async")
    def handler(event):
        seen["thread"] = threading.current_thread().name
        done.set()

    InProcessEventBus(run_async=True).publish("test.async", {})
    assert done.wait(timeout=5)
    assert seen["thread"].startswith("events")
