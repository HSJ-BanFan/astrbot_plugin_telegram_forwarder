---
name: qq-sender-module-split-or-refactor-workflow
description: Workflow command scaffold for qq-sender-module-split-or-refactor-workflow in astrbot_plugin_telegram_forwarder.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /qq-sender-module-split-or-refactor-workflow

Use this workflow when working on **qq-sender-module-split-or-refactor-workflow** in `astrbot_plugin_telegram_forwarder`.

## Goal

Refactors the QQ sender by splitting logic into new module files, updating the main sender, and ensuring tests are updated to cover new boundaries.

## Common Files

- `core/senders/qq.py`
- `core/senders/qq_batch_builder.py`
- `core/senders/qq_circuit.py`
- `core/senders/qq_dispatcher.py`
- `core/senders/qq_media.py`
- `core/senders/qq_reply_preview.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Create new core/senders/qq_*.py files for separated logic (e.g., qq_batch_builder.py, qq_circuit.py, qq_send_prep.py, etc.)
- Refactor core/senders/qq.py to delegate to new modules
- Update or add tests in tests/test_qq_sender.py and possibly tests/conftest.py
- Merge or rebase branches as needed

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.