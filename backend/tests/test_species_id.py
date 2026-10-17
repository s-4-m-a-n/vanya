from datetime import UTC, datetime

import pytest

from app.adapters.llm import LLMError
from app.adapters.species_id import IdContext, SpeciesIdError
from app.adapters.species_id.llm import LlmSpeciesIdentifier, build_prompt, normalise_name
from app.adapters.species_id.stub import StubSpeciesIdentifier


class FakeLLM:
    provider = "fake"
    model = "fake-model"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


CONTEXT = IdContext(
    latitude=12.97521, longitude=77.59456, observed_at=datetime(2026, 10, 1, tzinfo=UTC)
)


def test_llm_identifier_normalises_sorts_and_truncates():
    llm = FakeLLM(
        {
            "is_organism": True,
            "candidates": [
                {
                    "scientific_name": "corvus  SPLENDENS",
                    "common_name": "House Crow",
                    "taxon_group": "bird",
                    "confidence": 0.2,
                },
                {
                    "scientific_name": "Corvus macrorhynchos",
                    "common_name": "Jungle Crow",
                    "taxon_group": "bird",
                    "confidence": 1.7,
                },
                {
                    "scientific_name": "Corvus corax",
                    "common_name": "",
                    "taxon_group": "weird",
                    "confidence": "n/a",
                },
                {
                    "scientific_name": "  ",
                    "common_name": "x",
                    "taxon_group": "bird",
                    "confidence": 0.5,
                },
            ],
            "habitat": "urban_garden",
            "explanation": "Grey nape.",
        }
    )
    result = LlmSpeciesIdentifier(lambda: llm).identify(b"img", "image/jpeg", CONTEXT, top_k=2)

    assert result.provider == "fake" and result.model == "fake-model"
    assert [c.scientific_name for c in result.candidates] == [
        "Corvus macrorhynchos",
        "Corvus splendens",
    ]
    assert result.candidates[0].confidence == 1.0  # clamped
    assert result.habitat == "urban_garden"
    assert llm.calls[0]["images"][0].data == b"img"
    assert llm.calls[0]["schema"]["properties"]["candidates"]["maxItems"] == 2
    item_schema = llm.calls[0]["schema"]["properties"]["candidates"]["items"]
    assert "english_name" in item_schema["required"]


def test_unknown_group_and_bad_confidence_are_sanitised():
    llm = FakeLLM(
        {
            "is_organism": True,
            "candidates": [
                {
                    "scientific_name": "Corvus corax",
                    "common_name": "",
                    "taxon_group": "weird",
                    "confidence": "n/a",
                }
            ],
            "habitat": "not-a-habitat",
            "explanation": "",
        }
    )
    result = LlmSpeciesIdentifier(lambda: llm).identify(b"img", "image/jpeg", CONTEXT, top_k=3)
    candidate = result.candidates[0]
    assert (candidate.taxon_group, candidate.confidence, candidate.common_name) == (
        "other",
        0.0,
        None,
    )
    assert result.habitat is None and result.explanation is None


def test_photo_flags_are_limited_to_known_values():
    llm = FakeLLM(
        {
            "is_organism": True,
            "candidates": [],
            "habitat": "forest",
            "photo_flags": ["watermark", "made_up", "watermark", "screenshot"],
            "explanation": "",
        }
    )
    result = LlmSpeciesIdentifier(lambda: llm).identify(b"img", "image/jpeg", CONTEXT, top_k=3)
    assert result.photo_flags == ["screenshot", "watermark"]
    assert "photo_flags" in llm.calls[0]["schema"]["required"]


def test_prompt_sends_only_coarse_location():
    prompt = build_prompt(CONTEXT, top_k=3)
    assert "13.0, 77.6" in prompt
    assert "12.975" not in prompt and "77.594" not in prompt
    assert "2026-10" in prompt


def test_prompt_asks_for_user_language():
    prompt = build_prompt(IdContext(locale="hi"), top_k=3)
    assert "in Hindi" in prompt


def test_llm_failure_becomes_species_id_error():
    identifier = LlmSpeciesIdentifier(lambda: FakeLLM(error=LLMError("quota")))
    with pytest.raises(SpeciesIdError, match="quota"):
        identifier.identify(b"img", "image/jpeg", CONTEXT, top_k=3)


def test_unconfigured_llm_becomes_species_id_error():
    def factory():
        raise LLMError("No LLM configured")

    with pytest.raises(SpeciesIdError):
        LlmSpeciesIdentifier(factory).identify(b"img", "image/jpeg", CONTEXT, top_k=3)


def test_stub_is_deterministic():
    stub = StubSpeciesIdentifier()
    first = stub.identify(b"photo-1", "image/jpeg", CONTEXT, top_k=3)
    assert first == stub.identify(b"photo-1", "image/jpeg", CONTEXT, top_k=3)
    assert len(first.candidates) == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("passer DOMESTICUS", "Passer domesticus"), ("  Pavo   cristatus ", "Pavo cristatus")],
)
def test_normalise_name(raw, expected):
    assert normalise_name(raw) == expected
