import base64
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.adapters.llm.base import ImageInput, LLMClient, LLMError, parse_json_object
from app.config import APP_NAME

logger = logging.getLogger(__name__)


class OpenRouterClient(LLMClient):
    """OpenRouter's OpenAI-compatible chat completions API (local stand-in for Vertex AI)."""

    provider = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        reasoning_effort: str,
        max_output_tokens: int,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": APP_NAME},
        )

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
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            encoded = base64.b64encode(image.data).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
                }
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": self._max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "reasoning": {"effort": self._reasoning_effort, "exclude": True},
            # Only route to upstream providers that honour response_format.
            "provider": {"require_parameters": True},
        }
        try:
            response = self._http.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc
        if response.status_code != 200:
            raise LLMError(f"OpenRouter returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        if "error" in data:
            raise LLMError(f"OpenRouter error: {data['error']}")
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenRouter response: {str(data)[:300]}") from exc
        logger.info("openrouter %s usage=%s", self.model, data.get("usage"))
        return parse_json_object(text)
