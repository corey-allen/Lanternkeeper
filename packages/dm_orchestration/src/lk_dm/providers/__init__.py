"""Provider adapters and the factory that enforces remote-transmission policy."""

from __future__ import annotations

from urllib.parse import urlparse

from .base import (
    Message,
    ModelProvider,
    ProviderError,
    ProviderResponseInvalid,
    ProviderTimeout,
    ProviderUnavailable,
    RemoteTransmissionDisabled,
    RetryPolicy,
    call_with_policy,
)
from .fake import FakeProvider
from .openai_compat import OpenAICompatibleProvider

DEFAULT_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"})


def is_remote_url(url: str, local_hosts: frozenset[str] = DEFAULT_LOCAL_HOSTS) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host not in local_hosts


def build_provider(
    kind: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    allow_remote: bool = False,
    local_hosts: frozenset[str] = DEFAULT_LOCAL_HOSTS,
) -> ModelProvider:
    if kind == "fake":
        return FakeProvider()
    if kind == "openai_compatible":
        if not base_url:
            raise ProviderError("LK_AI_BASE_URL is required for the openai_compatible provider")
        remote = is_remote_url(base_url, local_hosts)
        if remote and not allow_remote:
            raise RemoteTransmissionDisabled(
                "The configured model endpoint is remote but LK_AI_ALLOW_REMOTE is false. "
                "Campaign and source text would leave this machine; enable it explicitly."
            )
        return OpenAICompatibleProvider(base_url=base_url, api_key=api_key, is_remote=remote)
    raise ProviderError(f"unknown provider kind {kind!r}")


__all__ = [
    "FakeProvider",
    "Message",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderResponseInvalid",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RemoteTransmissionDisabled",
    "RetryPolicy",
    "build_provider",
    "call_with_policy",
    "is_remote_url",
]
