from collections.abc import Sequence
from typing import Any

from app.adapters.llm.base import ImageInput, LLMClient, LLMError, parse_json_object


class VertexGeminiClient(LLMClient):
    """Gemini on Vertex AI via the Google Gen AI SDK. Requires the `gcp` extra.

    The runtime service account needs roles/aiplatform.user.
    """

    provider = "vertex"

    def __init__(
        self,
        project: str,
        location: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
    ) -> None:
        from google import genai

        self._client = genai.Client(vertexai=True, project=project, location=location)
        self.model = model.removeprefix("google/")
        self._thinking_level = reasoning_effort.upper()
        self._max_output_tokens = max_output_tokens

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
        from google.genai import types

        contents: list[Any] = [
            types.Part.from_bytes(data=image.data, mime_type=image.mime_type) for image in images
        ]
        contents.append(prompt)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=self._max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level),
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except Exception as exc:
            raise LLMError(f"Vertex AI request failed: {exc}") from exc
        return parse_json_object(response.text)
