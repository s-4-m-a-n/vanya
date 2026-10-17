import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    topic: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))


Handler = Callable[[Event], None]

# Handlers are plain functions keyed by topic. Locally the in-process bus calls them
# directly; on GCP a Pub/Sub push subscription delivers to an API endpoint that calls
# dispatch() with the same registry, so handler code is identical in both environments.
_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(topic: str) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        _handlers[topic].append(handler)
        return handler

    return register


def dispatch(event: Event, raise_errors: bool = False) -> None:
    """Run every handler for the event's topic.

    In-process delivery logs failures so a failing consumer never fails the publisher.
    Pub/Sub push delivery passes raise_errors=True so the endpoint returns an error and
    Pub/Sub retries the message.
    """
    for handler in _handlers.get(event.topic, []):
        try:
            handler(event)
        except Exception:
            if raise_errors:
                raise
            logger.exception("Event handler %s failed for %s", handler.__name__, event.topic)


class EventBus(ABC):
    """Async workflow and alert fan-out (Pub/Sub on GCP)."""

    @abstractmethod
    def publish(self, topic: str, payload: dict[str, Any]) -> Event: ...
