"""Adapter for any server exposing an OpenAI-compatible Chat Completions API.

This covers local runtimes (Ollama, llama.cpp server, LM Studio, vLLM) as well as
hosted services. It uses plain HTTP via httpx; no vendor SDK is required.
"""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .base import Message, ProviderError, ProviderResponseInvalid, ProviderTimeout, ProviderUnavailable

T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, *, base_url: str, api_key: str | None, is_remote: bool,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.is_remote = is_remote
        self._transport = transport

    async def _post(self, payload: dict, timeout_seconds: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds, transport=self._transport) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"model request failed: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"model server returned {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(f"model server rejected request ({resp.status_code})")
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ProviderResponseInvalid("unexpected response shape") from exc

    async def generate_structured(
        self, messages: list[Message], response_schema: type[T], *, model: str, timeout_seconds: float
    ) -> T:
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": response_schema.__name__,
                                "schema": response_schema.model_json_schema()},
            },
        }
        content = await self._post(payload, timeout_seconds)
        try:
            return response_schema.model_validate(json.loads(content))
        except (ValueError, ValidationError) as exc:
            raise ProviderResponseInvalid("structured response failed schema validation") from exc

    async def generate_text(self, messages: list[Message], *, model: str, timeout_seconds: float) -> str:
        payload = {"model": model, "messages": [m.model_dump() for m in messages], "temperature": 0.7}
        return (await self._post(payload, timeout_seconds)).strip()
