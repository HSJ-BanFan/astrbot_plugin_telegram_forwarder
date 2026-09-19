# 📖 Domain Model & Glossary (CONTEXT.md)

This glossary captures the ubiquitous language of the Telegram Forwarder plugin. It defines core concepts and invariants without implementation details.

---

### Core Domain Entities

#### `Forwarder`
The central message orchestration hub. Coordinates message ingestion from monitored Telegram channels, enforces screening and deduplication, manages pending message persistence, and dispatches messages to target channels and groups across supported platforms.

#### `AutoRecall`
The message lifecycle protection subsystem. Automatically revokes/deletes forwarded messages on target platforms after a configured delay to mitigate risk of reporting or policy penalties.

#### `RecallRegistry`
The central lifecycle registry for auto-recall timers. Tracks active asynchronous recall tasks, enforces bounded capacity, isolates transient platform failures, and guarantees graceful cancellation upon plugin shutdown or reconfiguration.

#### `Sender Protocol`
The abstraction contract bridging outbound message payloads to platform-specific APIs. Senders translate logical batches into native message formats and return dispatch receipts for auto-recall tracking.

#### `ContentScreeningPipeline`
The unified message screening and safety evaluation engine. Enforces multi-modal content boundaries across a dual-stage gate: fast-path text/regex filtering at ingestion, and deep visual/QR risk/AI moderation at dispatch. Encapsulates channel-specific rule hierarchies, pixel and size boundaries, and cyclic LLM call budgets.

#### `ScreeningVerdict`
The definitive, immutable outcome of a screening evaluation. Encapsulates the binary access decision (`ALLOW` or `DROP`), human-readable diagnostic reason, evaluation stage, and matched rule or keyword identifier.

#### `PlatformDirectory`
The cached directory registry for external target channels and groups (QQ groups and Telegram channels). Enforces concurrency locking, TTL freshness evaluation, transient failure backoff cooldowns, and fallback preservation of user-configured targets. Relies on platform-specific directory adapters to fetch live entity listings.

#### `DirectoryAdapter`
The protocol adapter bridging a specific chat platform (OneBot 11 / NapCat or Telethon MTProto) to the `PlatformDirectory`. Supplies live fetch routines, entity identifier extractors, cache status markers, and synthetic fallback constructors for configured targets.
