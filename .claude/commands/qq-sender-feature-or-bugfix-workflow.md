---
name: qq-sender-feature-or-bugfix-workflow
description: Workflow command scaffold for qq-sender-feature-or-bugfix-workflow in astrbot_plugin_telegram_forwarder.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /qq-sender-feature-or-bugfix-workflow

Use this workflow when working on **qq-sender-feature-or-bugfix-workflow** in `astrbot_plugin_telegram_forwarder`.

## Goal

Implements or fixes features in the QQ sender pipeline, often involving core/senders/qq.py and its related modules, with corresponding updates to tests/test_qq_sender.py.

## Common Files

- `core/senders/qq.py`
- `core/senders/qq_dispatcher.py`
- `core/senders/qq_media.py`
- `core/senders/qq_file_fallback.py`
- `core/senders/qq_types.py`
- `tests/test_qq_sender.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit or create files in core/senders/qq*.py (e.g., qq.py, qq_dispatcher.py, qq_media.py, qq_file_fallback.py, qq_types.py)
- Update or add tests in tests/test_qq_sender.py
- Optionally update configuration or command files (e.g., core/commands.py, _conf_schema.json, README.md) if new options or help text are needed

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.