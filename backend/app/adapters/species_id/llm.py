import re
from collections.abc import Callable

from app.adapters.llm import ImageInput, LLMClient, LLMError
from app.adapters.species_id.base import (
    Candidate,
    IdContext,
    IdResult,
    SpeciesIdentifier,
    SpeciesIdError,
)
from app.models import Habitat

LANGUAGES = {"en": "English", "hi": "Hindi", "es": "Spanish"}
GROUPS = ["animal", "bird", "plant", "other"]
PHOTO_FLAGS = ["screenshot", "illustration", "watermark", "captive_or_cultivated"]

SYSTEM_PROMPT = (
    "You are an expert field naturalist identifying wild animals, birds and plants from "
    "citizen-science photos for a conservation database. Accuracy matters more than "
    "confidence: conservation decisions depend on this data, so be honest about uncertainty."
)


def response_schema(top_k: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "is_organism": {"type": "boolean"},
            "candidates": {
                "type": "array",
                "maxItems": top_k,
                "items": {
                    "type": "object",
                    "properties": {
                        "scientific_name": {"type": "string"},
                        "common_name": {"type": "string"},
                        "english_name": {"type": "string"},
                        "taxon_group": {"type": "string", "enum": GROUPS},
                        "confidence": {"type": "number"},
                    },
                    "required": [
                        "scientific_name",
                        "common_name",
                        "english_name",
                        "taxon_group",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
            "habitat": {"type": "string", "enum": [h.value for h in Habitat]},
            "photo_flags": {"type": "array", "items": {"type": "string", "enum": PHOTO_FLAGS}},
            "explanation": {"type": "string"},
        },
        "required": ["is_organism", "candidates", "habitat", "photo_flags", "explanation"],
        "additionalProperties": False,
    }


def build_prompt(context: IdContext, top_k: int) -> str:
    language = LANGUAGES.get(context.locale, "English")
    hints = []
    # Coarse location only (~11 km): enough for range plausibility without sending
    # precise coordinates of possibly sensitive species to a third-party model.
    if context.latitude is not None and context.longitude is not None:
        hints.append(f"approximate location {context.latitude:.1f}, {context.longitude:.1f}")
    if context.observed_at is not None:
        hints.append(f"month {context.observed_at:%Y-%m}")
    context_line = f"Observation context: {'; '.join(hints)}.\n" if hints else ""
    return (
        "Identify the organism that is the main subject of this photo.\n"
        f"{context_line}"
        "Rules:\n"
        f"- Return up to {top_k} candidate species, most likely first, using accepted binomial "
        "scientific names (genus and species). If you can only narrow it to a genus, give the "
        "most likely species in that genus with a lower confidence.\n"
        "- confidence is your probability (0 to 1) that the candidate is correct; the values "
        "must not sum to more than 1. Use the location and month to favour species that occur "
        "there at that time.\n"
        '- taxon_group: "bird" for birds; "animal" for mammals, reptiles and amphibians; '
        '"plant" for plants; "other" for anything else (insects, fish, fungi, people).\n'
        "- If no animal, bird or plant is clearly visible, set is_organism to false and return "
        "no candidates.\n"
        "- habitat: the habitat visible in the photo.\n"
        '- photo_flags: every one that applies, else none: "screenshot" (a screenshot or a '
        'photo of a screen or print), "illustration" (drawing, painting, toy, model or statue), '
        '"watermark" (visible watermark or stock-photo marks), "captive_or_cultivated" (zoo, '
        "cage, aquarium, pet, farm animal, potted or deliberately planted ornamental).\n"
        f"- common_name in {language} if a widely used one exists, otherwise in English; "
        "english_name is the widely used English common name.\n"
        f"- explanation: one or two sentences in {language} on the key identifying features."
    )


def normalise_name(name: str) -> str:
    parts = re.sub(r"\s+", " ", name).strip().split(" ")
    if not parts or not parts[0]:
        return ""
    return " ".join([parts[0].capitalize(), *(p.lower() for p in parts[1:])])


class LlmSpeciesIdentifier(SpeciesIdentifier):
    """Species ID with a multimodal LLM (Gemini), via whichever LLM provider is configured."""

    def __init__(self, llm_factory: Callable[[], LLMClient]) -> None:
        self._llm_factory = llm_factory

    def identify(self, image: bytes, mime_type: str, context: IdContext, top_k: int) -> IdResult:
        try:
            llm = self._llm_factory()
            data = llm.generate_json(
                system=SYSTEM_PROMPT,
                prompt=build_prompt(context, top_k),
                schema=response_schema(top_k),
                schema_name="species_identification",
                images=[ImageInput(image, mime_type)],
            )
        except LLMError as exc:
            raise SpeciesIdError(str(exc)) from exc

        candidates = []
        for item in data.get("candidates") or []:
            name = normalise_name(str(item.get("scientific_name", "")))
            if not name:
                continue
            try:
                confidence = min(max(float(item.get("confidence", 0)), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            group = item.get("taxon_group")
            candidates.append(
                Candidate(
                    scientific_name=name,
                    common_name=(item.get("common_name") or None),
                    taxon_group=group if group in GROUPS else "other",
                    confidence=confidence,
                    english_name=(item.get("english_name") or None),
                )
            )
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        habitat = data.get("habitat")
        return IdResult(
            provider=llm.provider,
            model=llm.model,
            is_organism=bool(data.get("is_organism", bool(candidates))),
            candidates=candidates[:top_k],
            habitat=habitat if habitat in {h.value for h in Habitat} else None,
            explanation=data.get("explanation") or None,
            photo_flags=sorted({f for f in data.get("photo_flags") or [] if f in PHOTO_FLAGS}),
        )
