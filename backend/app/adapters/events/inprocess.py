from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.adapters.events.base import Event, EventBus, dispatch


class InProcessEventBus(EventBus):
    """In-process delivery.

    Async mode hands events to a small thread pool, mirroring Pub/Sub on GCP: the
    publisher returns at once and consumers catch up. Sync mode keeps tests deterministic.
    """

    def __init__(self, run_async: bool = False) -> None:
        self.published: list[Event] = []
        self._pool = (
            ThreadPoolExecutor(max_workers=4, thread_name_prefix="events") if run_async else None
        )

    def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        event = Event(topic=topic, payload=payload)
        self.published.append(event)
        if self._pool is not None:
            self._pool.submit(dispatch, event)
        else:
            dispatch(event)
        return event
