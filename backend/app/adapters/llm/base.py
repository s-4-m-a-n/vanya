import json
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    """Any failure to get a usable answer from the model."""


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str = "image/jpeg"


class LLMClient(ABC):
    """Multimodal LLM (Gemini on Vertex AI on GCP)."""

    provider: str
    model: str

    @abstractmethod
    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        schema_name: str,
        images: Sequence[ImageInput] = (),
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Return a JSON object conforming to `schema`."""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json_object(text: str | None) -> dict[str, Any]:
    if not text:
        raise LLMError("Model returned an empty response")
    try:
        value = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model returned invalid JSON: {text[:200]!r}") from exc
    if not isinstance(value, dict):
        raise LLMError("Model returned JSON that is not an object")
    return value
