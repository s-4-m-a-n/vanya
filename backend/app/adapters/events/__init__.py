from functools import lru_cache

from app.adapters.events.base import Event, EventBus, dispatch, subscribe
from app.config import get_settings


@lru_cache
def get_event_bus() -> EventBus:
    settings = get_settings()
    if settings.events_provider == "pubsub":
        from app.adapters.events.pubsub import PubSubEventBus

        if not settings.gcp_project_id:
            raise RuntimeError("GCP_PROJECT_ID must be set when EVENTS_PROVIDER=pubsub")
        return PubSubEventBus(settings.gcp_project_id, settings.pubsub_topic_prefix)

    from app.adapters.events.inprocess import InProcessEventBus

    return InProcessEventBus(run_async=settings.events_inprocess_async)


__all__ = ["Event", "EventBus", "dispatch", "get_event_bus", "subscribe"]
