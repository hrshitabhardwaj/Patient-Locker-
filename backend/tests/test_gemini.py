"""The Gemini client against recorded-style responses (no network)."""

import json

import httpx2
import pytest
from pydantic import BaseModel

from app.ai import llm
from app.ai.llm import GeminiClient, LLMResponseInvalid, LLMUnavailable


class Answer(BaseModel):
    document_type: str
    signed: bool


def answer(status: int, body: dict, seen: list | None = None):
    def post(url, **kwargs):
        if seen is not None:
            seen.append((url, kwargs))
        return httpx2.Response(status, json=body, request=httpx2.Request("POST", url))

    return post


def ok(text: str, finish: str = "STOP") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


@pytest.fixture
def client():
    GeminiClient._next_call_at = 0.0
    return GeminiClient("g-key", "gemini-flash-lite-latest", 30, requests_per_minute=0)


def test_request_shape_and_answer(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        llm.httpx2, "post", answer(200, ok('{"document_type": "bill", "signed": true}'), seen)
    )

    result = client.generate_json(
        system="Classify.", prompt="Page 1", schema=Answer, images=[b"\xff\xd8jpeg-bytes"]
    )

    assert result.value == Answer(document_type="bill", signed=True)
    assert result.model == "gemini:gemini-flash-lite-latest"
    [(url, kwargs)] = seen
    assert url.endswith("/models/gemini-flash-lite-latest:generateContent")
    assert kwargs["headers"] == {"x-goog-api-key": "g-key"}
    body = kwargs["json"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0
    instructions = body["systemInstruction"]["parts"][0]["text"]
    assert instructions.startswith("Classify.") and '"signed"' in instructions
    text_part, image_part = body["contents"][0]["parts"]
    assert text_part == {"text": "Page 1"}
    assert image_part["inlineData"]["mimeType"] == "image/jpeg"


def test_code_fenced_json_is_accepted(client, monkeypatch):
    fenced = '```json\n{"document_type": "bill", "signed": false}\n```'
    monkeypatch.setattr(llm.httpx2, "post", answer(200, ok(fenced)))

    assert client.generate_json(system="s", prompt="p", schema=Answer).value.signed is False


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (429, {"error": {"message": "quota"}}, LLMUnavailable),
        (503, {}, LLMUnavailable),
        (403, {}, LLMUnavailable),
        (400, {}, LLMResponseInvalid),
        (200, ok('{"document_type": "bill"}'), LLMResponseInvalid),
        (200, ok('{"document_type": "bi', finish="MAX_TOKENS"), LLMResponseInvalid),
        (200, {"candidates": []}, LLMResponseInvalid),
    ],
)
def test_errors(client, monkeypatch, status, body, error):
    monkeypatch.setattr(llm.httpx2, "post", answer(status, body))

    with pytest.raises(error):
        client.generate_json(system="s", prompt="p", schema=Answer)


def test_network_failure_is_unavailable(client, monkeypatch):
    def refuse(url, **kwargs):
        raise httpx2.ConnectError("refused")

    monkeypatch.setattr(llm.httpx2, "post", refuse)
    with pytest.raises(LLMUnavailable):
        client.generate_json(system="s", prompt="p", schema=Answer)


def test_missing_key_never_calls_out(monkeypatch):
    monkeypatch.setattr(llm.httpx2, "post", lambda *a, **k: pytest.fail("called"))
    with pytest.raises(LLMUnavailable):
        GeminiClient("", "m", 30, 0).generate_json(system="s", prompt="p", schema=Answer)


def test_calls_are_spaced_to_the_rate_limit(monkeypatch):
    GeminiClient._next_call_at = 0.0
    clock = {"now": 1000.0}
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(llm.time, "sleep", fake_sleep)
    reply = ok(json.dumps({"document_type": "x", "signed": True}))
    monkeypatch.setattr(llm.httpx2, "post", answer(200, reply))
    client = GeminiClient("k", "m", 30, requests_per_minute=12)

    for _ in range(3):
        client.generate_json(system="s", prompt="p", schema=Answer)

    assert sleeps == [5.0, 5.0]


def test_provider_switch(monkeypatch):
    from pydantic import SecretStr

    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("k"))
    assert llm.get_llm_client().model_name == "gemini:gemini-flash-lite-latest"
