# ADR-0005: Provider-neutral AI, offline default, remote opt-in

**Status:** Accepted (2026-10-29)

**Decision.** `ModelProvider` protocol with a deterministic `FakeProvider` (default) and an
`OpenAICompatibleProvider` over plain HTTP, which covers local runtimes and hosted services. No
vendor SDK or vendor tool-calling format. Endpoints whose host is not in `LK_AI_LOCAL_HOSTS` are
refused unless `LK_AI_ALLOW_REMOTE=true`; the UI always shows whether content leaves the server.
Timeouts and retry policy are applied by the orchestrator.

**Consequences.** Works fully offline for tests and development. Real-model quality is not yet
evaluated. A native adapter for a specific vendor can be added behind the same protocol.
