"""Provider-neutral model interface."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

from ..schemas import Message

log = logging.getLogger("lk_dm.provider")

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


class ProviderError(Exception):
    code = "PROVIDER_ERROR"
    retryable = False


class ProviderTimeout(ProviderError):
    code = "PROVIDER_TIMEOUT"
    retryable = True


class ProviderUnavailable(ProviderError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True


class ProviderResponseInvalid(ProviderError):
    code = "PROVIDER_RESPONSE_INVALID"


class RemoteTransmissionDisabled(ProviderError):
    code = "REMOTE_TRANSMISSION_DISABLED"


class ModelProvider(Protocol):
    name: str
    is_remote: bool

    async def generate_structured(
        self, messages: list[Message], response_schema: type[T], *, model: str, timeout_seconds: float
    ) -> T: ...

    async def generate_text(
        self, messages: list[Message], *, model: str, timeout_seconds: float
    ) -> str: ...


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_attempts: int = 2
    backoff_seconds: float = 0.5


async def call_with_policy(
    fn: Callable[[], Awaitable[R]], *, timeout_seconds: float, policy: RetryPolicy
) -> R:
    """Run ``fn`` with a hard timeout and retry only retryable provider errors."""
    last: ProviderError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await asyncio.wait_for(fn(), timeout=timeout_seconds)
        except TimeoutError:
            last = ProviderTimeout(f"model call exceeded {timeout_seconds}s")
        except ProviderError as exc:
            if not exc.retryable:
                raise
            last = exc
        log.warning("provider call failed", extra={"attempt": attempt, "code": last.code})
        if attempt < policy.max_attempts:
            await asyncio.sleep(policy.backoff_seconds * attempt)
    assert last is not None
    raise last
