import hashlib

from app.adapters.species_id.base import Candidate, IdContext, IdResult, SpeciesIdentifier

# Species from the seed list so stub results resolve to real taxa.
_SPECIES = [
    ("Passer domesticus", "House Sparrow", "bird"),
    ("Columba livia", "Rock Dove", "bird"),
    ("Corvus splendens", "House Crow", "bird"),
    ("Vulpes vulpes", "Red Fox", "animal"),
    ("Sciurus carolinensis", "Eastern Gray Squirrel", "animal"),
    ("Taraxacum officinale", "Common Dandelion", "plant"),
    ("Quercus robur", "English Oak", "plant"),
]
_CONFIDENCES = [0.86, 0.09, 0.03, 0.01, 0.005]


class StubSpeciesIdentifier(SpeciesIdentifier):
    """Deterministic fake for development and tests: same photo, same answer."""

    def identify(self, image: bytes, mime_type: str, context: IdContext, top_k: int) -> IdResult:
        start = hashlib.sha256(image).digest()[0] % len(_SPECIES)
        picks = [_SPECIES[(start + i) % len(_SPECIES)] for i in range(min(top_k, len(_SPECIES)))]
        return IdResult(
            provider="stub",
            candidates=[
                Candidate(name, common, group, conf, english_name=common)
                for (name, common, group), conf in zip(picks, _CONFIDENCES, strict=False)
            ],
            habitat="urban_garden",
            explanation="Stub identifier: not a real identification.",
        )
