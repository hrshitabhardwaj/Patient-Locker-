"""The one place the application talks to a language model.

Every caller asks for JSON matching a Pydantic model and gets back a validated instance,
whichever provider is configured. Providers are switched in .env (LLM_PROVIDER).
"""

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import httpx2
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """The model could not be reached or is not loaded. Temporary: retry later."""


class LLMResponseInvalid(Exception):
    """The model answered, but not with valid JSON for the requested schema."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    model: str
    duration_ms: int


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate_json[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], images: list[bytes] | None = None
    ) -> LLMResult[T]: ...


class OllamaClient:
    """Local models served by Ollama (development)."""

    # Hard cap on generated tokens. Small models sometimes loop; this stops them.
    MAX_OUTPUT_TOKENS = 1024

    def __init__(self, base_url: str, model: str, timeout_seconds: int):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    @property
    def model_name(self) -> str:
        return f"ollama:{self._model}"

    def generate_json[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], images: list[bytes] | None = None
    ) -> LLMResult[T]:
        user_message: dict = {"role": "user", "content": prompt}
        if images:
            user_message["images"] = [base64.b64encode(image).decode() for image in images]
        body = {
            "model": self._model,
            "stream": False,
            "think": False,
            "format": schema.model_json_schema(),
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
                "num_predict": self.MAX_OUTPUT_TOKENS,
            },
            "messages": [{"role": "system", "content": system}, user_message],
        }

        started = time.perf_counter()
        try:
            response = httpx2.post(f"{self._base_url}/api/chat", json=body, timeout=self._timeout)
        except httpx2.TransportError as exc:
            raise LLMUnavailable(f"Ollama not reachable: {type(exc).__name__}") from None
        duration_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 404:
            raise LLMUnavailable(f"Model {self._model} is not installed in Ollama")
        if response.status_code >= 500:
            raise LLMUnavailable(f"Ollama error {response.status_code}")
        if response.status_code >= 400:
            raise LLMResponseInvalid(f"Ollama rejected the request ({response.status_code})")

        payload = response.json()
        if payload.get("done_reason") == "length":
            raise LLMResponseInvalid("Model output was cut off at the token limit")
        try:
            value = schema.model_validate_json(payload["message"]["content"])
        except (KeyError, ValidationError):
            raise LLMResponseInvalid("Model output did not match the schema") from None
        logger.info(
            "LLM call model=%s duration_ms=%d prompt_tokens=%s output_tokens=%s",
            self._model,
            duration_ms,
            payload.get("prompt_eval_count"),
            payload.get("eval_count"),
        )
        return LLMResult(value=value, model=self.model_name, duration_ms=duration_ms)


class GeminiClient:
    """Google's Gemini API (generativelanguage.googleapis.com), usable on hosts without Ollama.

    The JSON schema goes into the instructions and the answer is validated here, rather than
    relying on Gemini's own schema support, so every caller's schema works unchanged.

    The free tier allows only a few requests a minute (and a limited number a day), so calls
    are spaced out; a refusal for quota raises LLMUnavailable, which callers already treat as
    "retry later" or "carry on with rules only". Note that on the free tier Google may use
    the content sent (document page images and text) to improve its products.
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    MAX_OUTPUT_TOKENS = 4096

    _lock = threading.Lock()
    _next_call_at = 0.0

    def __init__(self, api_key: str, model: str, timeout_seconds: int, requests_per_minute: int):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0

    @property
    def model_name(self) -> str:
        return f"gemini:{self._model}"

    def _wait_for_turn(self) -> None:
        with GeminiClient._lock:
            now = time.monotonic()
            wait = GeminiClient._next_call_at - now
            GeminiClient._next_call_at = max(now, GeminiClient._next_call_at) + self._interval
        if wait > 0:
            time.sleep(wait)

    def generate_json[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], images: list[bytes] | None = None
    ) -> LLMResult[T]:
        if not self._api_key:
            raise LLMUnavailable("Gemini is not configured (GEMINI_API_KEY)")
        instructions = (
            f"{system}\n\nAnswer with a single JSON object that matches this JSON Schema, "
            f"and nothing else:\n{json.dumps(schema.model_json_schema())}"
        )
        parts: list[dict] = [{"text": prompt}]
        for image in images or []:
            mime = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
            parts.append(
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(image).decode()}}
            )
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "maxOutputTokens": self.MAX_OUTPUT_TOKENS,
            },
        }

        self._wait_for_turn()
        started = time.perf_counter()
        try:
            response = httpx2.post(
                f"{self.BASE_URL}/models/{self._model}:generateContent",
                json=body,
                headers={"x-goog-api-key": self._api_key},
                timeout=self._timeout,
            )
        except httpx2.TransportError as exc:
            raise LLMUnavailable(f"Gemini not reachable: {type(exc).__name__}") from None
        duration_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 429:
            raise LLMUnavailable("Gemini quota or rate limit reached")
        if response.status_code >= 500:
            raise LLMUnavailable(f"Gemini error {response.status_code}")
        if response.status_code in (401, 403, 404):
            # Wrong key or model name: a setup problem, not a bad answer. Retry later.
            raise LLMUnavailable(f"Gemini refused the request ({response.status_code})")
        if response.status_code >= 400:
            raise LLMResponseInvalid(f"Gemini rejected the request ({response.status_code})")

        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            raise LLMResponseInvalid("Gemini returned no answer")
        candidate = candidates[0]
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMResponseInvalid("Model output was cut off at the token limit")
        text = "".join(
            part.get("text", "")
            for part in (candidate.get("content") or {}).get("parts") or []
            if not part.get("thought")
        )
        try:
            value = schema.model_validate_json(_strip_code_fence(text))
        except ValidationError:
            raise LLMResponseInvalid("Model output did not match the schema") from None
        usage = payload.get("usageMetadata") or {}
        logger.info(
            "LLM call model=%s duration_ms=%d prompt_tokens=%s output_tokens=%s",
            self._model,
            duration_ms,
            usage.get("promptTokenCount"),
            usage.get("candidatesTokenCount"),
        )
        return LLMResult(value=value, model=self.model_name, duration_ms=duration_ms)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def get_llm_client() -> LLMClient | None:
    """The configured client, or None when AI features are switched off."""
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaClient(
            settings.ollama_base_url, settings.ollama_model, settings.llm_timeout_seconds
        )
    if settings.llm_provider == "gemini":
        return GeminiClient(
            settings.gemini_api_key.get_secret_value(),
            settings.gemini_model,
            settings.llm_timeout_seconds,
            settings.gemini_requests_per_minute,
        )
    if settings.llm_provider == "anthropic":
        # The hosted adapter is added once a company API key exists to test it with.
        logger.warning("LLM_PROVIDER=anthropic is not available yet; AI features are off")
    return None
