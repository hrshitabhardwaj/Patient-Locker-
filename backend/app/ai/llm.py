"""The one place the application talks to a language model.

Every caller asks for JSON matching a Pydantic model and gets back a validated instance,
whichever provider is configured. Providers are switched in .env (LLM_PROVIDER).
"""

import base64
import logging
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
            response = httpx2.post(
                f"{self._base_url}/api/chat", json=body, timeout=self._timeout
            )
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


def get_llm_client() -> LLMClient | None:
    """The configured client, or None when AI features are switched off."""
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaClient(
            settings.ollama_base_url, settings.ollama_model, settings.llm_timeout_seconds
        )
    if settings.llm_provider == "anthropic":
        # The hosted adapter is added once a company API key exists to test it with.
        logger.warning("LLM_PROVIDER=anthropic is not available yet; AI features are off")
    return None
