import json
from typing import Any

from app.adapters.events.base import Event, EventBus


class PubSubEventBus(EventBus):
    """Google Cloud Pub/Sub publisher. Requires the `gcp` extra.

    Topic `observation.verified` maps to `projects/<project>/topics/<prefix>observation.verified`.
    """

    def __init__(self, project: str, topic_prefix: str) -> None:
        from google.cloud import pubsub_v1

        self._publisher = pubsub_v1.PublisherClient()
        self._project = project
        self._prefix = topic_prefix

    def publish(self, topic: str, payload: dict[str, Any]) -> Event:
        event = Event(topic=topic, payload=payload)
        topic_path = self._publisher.topic_path(self._project, f"{self._prefix}{topic}")
        data = json.dumps(event.payload, default=str).encode()
        # The push envelope doesn't name the topic, so carry it as an attribute.
        future = self._publisher.publish(
            topic_path,
            data,
            topic=topic,
            event_id=event.id,
            published_at=event.published_at.isoformat(),
        )
        future.result(timeout=30)
        return event
