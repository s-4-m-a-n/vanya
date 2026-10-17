import base64
import json

import httpx
import pytest

from app.adapters.llm import ImageInput, LLMError
from app.adapters.llm.base import parse_json_object
from app.adapters.llm.openrouter import OpenRouterClient

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-test",
        model="google/gemini-3-flash-preview",
        base_url="https://openrouter.test/api/v1",
        reasoning_effort="low",
        max_output_tokens=2048,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_sends_structured_multimodal_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return _completion('{"ok": true}')

    result = _client(handler).generate_json(
        system="sys",
        prompt="identify",
        schema=SCHEMA,
        schema_name="test",
        images=[ImageInput(b"jpeg-bytes")],
    )
    assert result == {"ok": True}

    request = seen["request"]
    assert request.url.path == "/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "google/gemini-3-flash-preview"
    assert body["response_format"]["json_schema"] == {
        "name": "test",
        "strict": True,
        "schema": SCHEMA,
    }
    assert body["reasoning"]["effort"] == "low"
    assert body["max_tokens"] == 2048
    assert body["provider"] == {"require_parameters": True}
    system, user = body["messages"]
    assert system == {"role": "system", "content": "sys"}
    text, image = user["content"]
    assert text == {"type": "text", "text": "identify"}
    expected = f"data:image/jpeg;base64,{base64.b64encode(b'jpeg-bytes').decode()}"
    assert image["image_url"]["url"] == expected


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": "bad key"}}),
        httpx.Response(200, json={"error": {"message": "upstream"}}),
        httpx.Response(200, json={"choices": []}),
        _completion("not json"),
        _completion("[1, 2]"),
    ],
    ids=["http-error", "error-body", "no-choices", "invalid-json", "not-object"],
)
def test_failures_raise_llm_error(response):
    with pytest.raises(LLMError):
        _client(lambda request: response).generate_json(
            system="s", prompt="p", schema=SCHEMA, schema_name="t"
        )


def test_network_error_raises_llm_error():
    def handler(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(LLMError):
        _client(handler).generate_json(system="s", prompt="p", schema=SCHEMA, schema_name="t")


def test_parses_fenced_json():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
