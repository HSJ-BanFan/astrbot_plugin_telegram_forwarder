# 📋 架构深化需求规格说明书: 内容审查管线与平台目录缓存接缝 (Architecture Deepening Spec)

> **版本**：v1.0 (Ready for Agent)  
> **关联 ADR**：[`ADR-001-auto-recall-lifecycle.md`](../adr/ADR-001-auto-recall-lifecycle.md)  
> **关联领域模型**：[`CONTEXT.md`](../../CONTEXT.md)  
> **关联工单**：[#58](https://github.com/HSJ-BanFan/astrbot_plugin_telegram_forwarder/issues/58) (子工单: [#59](https://github.com/HSJ-BanFan/astrbot_plugin_telegram_forwarder/issues/59), [#60](https://github.com/HSJ-BanFan/astrbot_plugin_telegram_forwarder/issues/60), [#61](https://github.com/HSJ-BanFan/astrbot_plugin_telegram_forwarder/issues/61))  
> **分类标签**：`ready-for-agent`

---

## Problem Statement

As a maintainer and developer of the Telegram Forwarder plugin, the codebase has accumulated architectural friction in two critical paths:

1. **Content Screening Fragmentation & Severed Modules**: The existing message filtering logic was fractured. A shallow module was extracted with an inflexible interface that was completely abandoned in actual production execution, forcing the central message orchestration coordinator to inline hundreds of lines of keyword matching, regex evaluation, image size inspection, local QR code scanning, and AI quota tracking. Understanding or modifying screening behavior requires jumping between multiple files and digging into internal state, while automated tests are forced to construct deeply nested mocks of internal variables rather than testing the real screening pipeline through a clean interface.
2. **Duplicated Platform Directory Caching**: Both QQ group selection and Telegram channel selection independently implement an almost identical concurrency and caching state machine (asynchronous locking, 3600s TTL freshness calculation, 20s transient failure cooldown, fallback merging of unindexed targets, and degradation status marking). This duplication bloats maintenance overhead, increases divergence risk under network flakiness, and lacks a unified seam for automated testing.

---

## Solution

1. **Deepen the Content Screening Pipeline**: Consolidate text keyword filtering, regex pattern matching, local QR risk analysis, and multimodal AI moderation into a cohesive, deep engine. The engine encapsulates channel-specific configuration inheritance, image size and pixel budgets, and cyclic LLM quotas behind a single, high-leverage evaluation seam returning a structured verdict. The central forwarder delegates screening at two well-defined stages (fast-path pre-queue rejection at capture time, and deep visual safety screening at dispatch time) without inspecting internal counters or managing media byte thresholds.
2. **Unify the Platform Directory Cache Seam**: Extract a single generic caching engine that manages concurrency locking, TTL invalidation, transient failure backoffs, and fallback merging. Pluggable platform adapters supply live fetch routines and entity normalization, allowing QQ group discovery and Telegram channel discovery to share 100% of their lifecycle logic while maintaining seamless backward compatibility with existing WebUI schemas.

---

## User Stories

1. As a plugin user, I want messages containing prohibited keywords or regex patterns to be rejected immediately upon capture, so that unwanted content never enters the pending message queue.
2. As a plugin user, I want custom channel-level screening configurations to override global filter rules, so that specific channels can have tailored moderation policies without breaking global defaults.
3. As a plugin user, I want promotional or malicious QR codes (e.g., loans, gambling, fraud) embedded in images to be detected locally before forwarding, so that destination groups are shielded from compliance risks.
4. As a bot administrator, I want AI vision safety analysis to run within a bounded per-cycle call budget, so that LLM token costs remain strictly controlled even during heavy Telegram post bursts.
5. As a bot administrator, I want the screening pipeline to automatically fall back to local QR and text filtering when the AI call budget is exhausted, so that message delivery continues reliably without total rejection.
6. As a bot administrator, I want images that exceed safety size thresholds or fail downloading to degrade gracefully to text-only evaluation, so that network or media hiccups do not crash or stall the dispatch queue.
7. As a plugin operator, I want structured diagnostic logs whenever a message is dropped, so that I can clearly understand which exact keyword, regex pattern, QR risk word, or AI moderation rule triggered the rejection.
8. As a WebUI dashboard user, I want the QQ group directory to load quickly and reliably, so that I can pick target forwarding groups without experiencing UI freezes or timeouts.
9. As a WebUI dashboard user, I want the Telegram channel directory to scan recent dialogs without blocking plugin startup, so that AstrBot starts instantly even when network proxies are temporarily offline.
10. As a WebUI dashboard user, I want previously configured group and channel targets to remain visible in selection pickers even when platform discovery APIs experience temporary network failures, so that my active configurations are not lost or blanked out.
11. As a WebUI dashboard user, I want platform directory caches to respect a short failure cooldown (20s) instead of the full TTL (3600s) upon an error, so that transient platform flakiness recovers promptly without manual cache purging.
12. As a plugin developer, I want all message safety and content filtering rules to be testable through a single high-level interface without mocking forwarder internals, so that test suites reliably verify real production behaviors.
13. As a plugin developer, I want all platform directory concurrency, TTL expiration, and fallback merging logic to be testable using lightweight dummy adapters, so that regression tests execute rapidly without live network connections.
14. As an automated agent or developer, I want existing public APIs and backward-compatible filter shims to remain intact, so that all 572 existing automated tests continue passing without regression.

---

## Implementation Decisions

### 1. Unified Content Screening Pipeline

- **Encapsulated Scope**: The pipeline combines text keyword search, regex validation, local QR decoding, AI moderation quota management, and image dimension/size boundary checks into a single deep engine.
- **Dual-Stage Gate**:
  - *Stage 1 (Ingestion / Pre-Queue)*: Evaluates fast-path text and regex rules against incoming channel posts during capture. Messages that fail are dropped immediately without queue persistence.
  - *Stage 2 (Dispatch / Post-Fetch)*: Evaluates deep multimodal content (local QR risk scanning and LLM visual moderation) after media retrieval.
- **Quota Ownership**: The cyclic LLM call budget is owned entirely by the pipeline. A dedicated reset method is called once per dispatch cycle.
- **Typed Verdict Contract**:
  ```python
  from dataclasses import dataclass
  from enum import Enum


  class VerdictAction(str, Enum):
    ALLOW = "allow"
    DROP = "drop"


  class ScreeningStage(str, Enum):
    INGESTION = "ingestion"
    DISPATCH = "dispatch"


  @dataclass(frozen=True, slots=True)
  class ScreeningVerdict:
    action: VerdictAction
    reason: str = ""
    stage: ScreeningStage = ScreeningStage.INGESTION
    matched_policy: str | None = None

    @property
    def is_allowed(self) -> bool:
      return self.action == VerdictAction.ALLOW
```
- **Internal Degradation**: If an image exceeds configured megabyte limits or download fails, the pipeline gracefully degrades to text-only evaluation and produces a definitive verdict rather than raising unhandled exceptions to the caller.
- **Backward-Compatible Shims**: Legacy filter entrypoints provide facade wrappers delegating to the pipeline to ensure existing test suites remain functional.

### 2. Generic Platform Directory Cache Seam

- **Generic Cache Engine**: Encapsulates asynchronous locking, 3600s TTL freshness evaluation, 20s failure short cooldowns, fallback merging of unindexed targets, and degradation status markers (`live`, `cached`, `configured`).
- **Protocol-Driven Adapters**: Platform-specific discovery logic (OneBot group queries vs. Telethon dialog scans) is decoupled into focused adapters that supply:
  - `fetch_items()`: Asynchronous probe returning raw platform records.
  - `get_id(item)`: Extracts the canonical target identifier.
  - `mark_cached(item)`: Marks an item as cached fallback.
  - `create_fallback(id)`: Constructs a synthetic record for user-configured targets not returned by live discovery.
- **Preheating Coordination**: The directory exposes an idempotent, non-blocking `warm_up(force=False)` method. Background startup tasks invoke this without blocking AstrBot initialization.
- **Schema Compatibility**: Data structures returned to callers retain 100% compatibility with existing WebUI frontend expectations (`groups`, `channels`, `available`, `message`).

---

## Testing Decisions

- **Test Quality Standard**: Tests must target only the external behavioral contract of the modules (inputs -> outputs / state transitions), never asserting private internal counters or patching internal helper methods.
- **Modules to be Tested**:
  1. `ContentScreeningPipeline`:
     - Fast-path keyword matching (case-insensitivity, substring vs. boundary).
     - Regex pattern validation and invalid pattern resilience.
     - Dual-stage execution: Stage 1 drops vs. Stage 2 deep visual drops.
     - Cyclic AI call budget decrement and fallback to local QR scanning when budget reaches zero.
     - Image byte size threshold enforcement and graceful degradation on corrupt media.
     - Configuration reload and channel override precedence.
  2. `PlatformDirectory`:
     - Concurrency locking: Simultaneous callers trigger only a single live probe.
     - TTL expiration: Fresh entries served from memory; expired entries trigger background refresh.
     - Failure cooldown: Unsuccessful probes initiate a 20s retry cooldown without blocking subsequent cache reads.
     - Fallback preservation: Previously cached live items are retained with `source='cached'` upon live fetch failure.
     - Configured target merging: Unindexed IDs from user settings are merged with `source='configured'`.
- **Prior Art**:
  - `tests/test_auto_recall.py`: Demonstrates zero-network asynchronous lifecycle testing with strict deterministic time control.
  - `tests/test_qq_circuit.py`: Demonstrates clean state-machine contract testing.

---

## Out of Scope

- Modifying the underlying AstrBot plugin event lifecycle (`star.Star`) or web route registration mechanism.
- Rewriting the WebUI frontend code (`web/` or `pages/dashboard/`).
- Altering the auto-recall lifecycle or modifying ADR-001 contracts (`RecallRegistry` / `AutoRecallManager`).
- Re-architecting the QQ media path mapping engine or Docker volume mounting semantics.

---

## Further Notes

- All changes maintain 100% strict type hints (Python 3.10+ compatible).
- Execution must maintain green status across the entire 572-item test suite.
