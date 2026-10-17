import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.handlers  # noqa: F401  (registers event handlers)
from app.api.routes import (
    admin,
    auth,
    events,
    health,
    insights,
    media,
    observations,
    orgs,
    public,
    reviews,
    rewards,
    taxa,
)
from app.config import APP_NAME, get_settings

API_PREFIX = "/api/v1"


logger = logging.getLogger("app")


def create_app() -> FastAPI:
    settings = get_settings()
    logger.warning(
        "providers: storage=%s events=%s llm=%s (%s) species_id=%s taxonomy=%s baseline=%s",
        settings.storage_provider,
        settings.events_provider,
        settings.llm_provider,
        settings.llm_model,
        settings.species_id_provider,
        settings.taxonomy_resolver,
        settings.occurrence_baseline,
    )
    app = FastAPI(title=f"{APP_NAME} API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    for module in (auth, admin, taxa, observations, reviews, rewards, public, orgs, insights):
        app.include_router(module.router, prefix=API_PREFIX)
    if settings.storage_provider == "local":
        app.include_router(media.router)
    if settings.events_provider == "pubsub":
        app.include_router(events.router)
    return app


app = create_app()
