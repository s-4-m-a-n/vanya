from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class SpeciesIdError(RuntimeError):
    """Identification unavailable; the observer can still pick a species manually."""


@dataclass(frozen=True)
class IdContext:
    latitude: float | None = None
    longitude: float | None = None
    observed_at: datetime | None = None
    locale: str = "en"


@dataclass(frozen=True)
class Candidate:
    scientific_name: str
    common_name: str | None  # in the observer's language
    # "animal" | "bird" | "plant" | "other" (other = outside v1 scope)
    taxon_group: str
    confidence: float
    english_name: str | None = None  # stored too, so every locale has a fallback name


@dataclass
class IdResult:
    provider: str
    model: str | None = None
    is_organism: bool = True
    candidates: list[Candidate] = field(default_factory=list)
    habitat: str | None = None
    explanation: str | None = None
    # Photo-level warnings: "screenshot", "illustration", "watermark", "captive_or_cultivated".
    photo_flags: list[str] = field(default_factory=list)


class SpeciesIdentifier(ABC):
    """Photo → ranked species candidates (Vertex AI vision / Gemini on GCP)."""

    @abstractmethod
    def identify(self, image: bytes, mime_type: str, context: IdContext, top_k: int) -> IdResult:
        """Raise SpeciesIdError when no identification can be produced."""
