from functools import lru_cache

from app.adapters.species_id.base import (
    Candidate,
    IdContext,
    IdResult,
    SpeciesIdentifier,
    SpeciesIdError,
)
from app.config import get_settings


@lru_cache
def get_species_identifier() -> SpeciesIdentifier:
    if get_settings().species_id_provider == "llm":
        from app.adapters.llm import get_llm
        from app.adapters.species_id.llm import LlmSpeciesIdentifier

        return LlmSpeciesIdentifier(get_llm)

    from app.adapters.species_id.stub import StubSpeciesIdentifier

    return StubSpeciesIdentifier()


__all__ = [
    "Candidate",
    "IdContext",
    "IdResult",
    "SpeciesIdError",
    "SpeciesIdentifier",
    "get_species_identifier",
]
