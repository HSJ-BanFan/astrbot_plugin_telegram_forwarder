```markdown
# astrbot_plugin_telegram_forwarder Development Patterns

> Auto-generated skill from repository analysis

## Overview

This skill teaches you how to contribute to the `astrbot_plugin_telegram_forwarder` Python codebase, which focuses on forwarding messages (notably for QQ and Telegram) with robust session handling, modular sender logic, and enhanced logging/debugging. You'll learn the project's coding conventions, how to structure commits, and how to follow key development workflows for adding features, refactoring, improving reliability, and enhancing diagnostics.

---

## Coding Conventions

- **Language:** Python
- **Framework:** None detected
- **File Naming:** Use `snake_case` for all file and module names.
  - Example: `qq_file_fallback.py`, `test_qq_sender.py`
- **Import Style:** Use relative imports within the package.
  - Example:
    ```python
    from .qq_media import QQMediaHandler
    from . import qq_types
    ```
- **Export Style:** Use named exports; avoid wildcard imports.
  - Example:
    ```python
    # In qq_media.py
    class QQMediaHandler:
        ...
    __all__ = ["QQMediaHandler"]
    ```
- **Commit Messages:** Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
  - Prefixes: `fix`, `refactor`, `feat`, `chore`
  - Example: `fix: handle QQ media fallback on error`
- **Code Style:** Keep lines concise and functions focused. Use clear, descriptive names.

---

## Workflows

### qq-sender-feature-or-bugfix-workflow

**Trigger:** When you want to add, refactor, or fix QQ sender logic (media handling, fallback, batching, logging, etc).

**Command:** `/update-qq-sender`

1. Edit or create files in `core/senders/qq*.py` (e.g., `qq.py`, `qq_dispatcher.py`, `qq_media.py`, `qq_file_fallback.py`, `qq_types.py`).
2. Update or add tests in `tests/test_qq_sender.py`.
3. Optionally update configuration or command files (`core/commands.py`, `_conf_schema.json`, `README.md`) if new options or help text are needed.
4. Commit with a message like:
    ```
    feat: add batching to QQ sender
    ```
5. Run tests to ensure everything passes.

**Example:**
```python
# core/senders/qq_batch_builder.py
class QQBatchBuilder:
    def build(self, messages):
        # batching logic
        return batched_messages
```

---

### qq-sender-module-split-or-refactor-workflow

**Trigger:** When you want to modularize or refactor QQ sender logic for maintainability.

**Command:** `/refactor-qq-sender`

1. Create new `core/senders/qq_*.py` files for separated logic (e.g., `qq_batch_builder.py`, `qq_circuit.py`, `qq_send_prep.py`).
2. Refactor `core/senders/qq.py` to delegate to new modules.
3. Update or add tests in `tests/test_qq_sender.py` and possibly `tests/conftest.py`.
4. Merge or rebase branches as needed.
5. Commit with a message like:
    ```
    refactor: split QQ sender into modules
    ```
6. Run tests to verify modularization.

**Example:**
```python
# core/senders/qq.py
from .qq_batch_builder import QQBatchBuilder

def send_messages(messages):
    batches = QQBatchBuilder().build(messages)
    # send logic
```

---

### session-or-forwarder-reliability-fix-workflow

**Trigger:** When you want to fix or enhance session management, retry, or forwarding logic.

**Command:** `/fix-session-forwarder`

1. Edit `core/client.py` and/or `core/forwarder.py` to fix session or forwarding logic.
2. Update or add tests in `tests/test_client_session_schema.py`, `tests/test_forwarder_send_pending.py`, or related files.
3. Optionally update `.gitignore` or configuration files if development artifacts or settings are affected.
4. Commit with a message like:
    ```
    fix: improve session retry logic
    ```
5. Run tests to ensure reliability fixes.

**Example:**
```python
# core/forwarder.py
def forward_message(session, message):
    try:
        session.send(message)
    except SessionError:
        session.reconnect()
        session.send(message)
```

---

### qq-sender-logging-or-debug-feature-workflow

**Trigger:** When you want to add or improve logging/diagnostics for QQ sender behavior.

**Command:** `/add-qq-logging`

1. Edit or create logging-related files in `core/senders/` (e.g., `qq_log_policy.py`, `qq.py`, `qq_dispatcher.py`, `qq_media.py`).
2. Update or add configuration in `_conf_schema.json` and command handling in `core/commands.py` if toggles are needed.
3. Add or update tests in `tests/test_qq_log_policy.py`, `tests/test_commands_debug.py`, and `tests/test_qq_sender.py`.
4. Commit with a message like:
    ```
    feat: add debug logging for QQ sender
    ```
5. Run tests to verify logging features.

**Example:**
```python
# core/senders/qq_log_policy.py
import logging

logger = logging.getLogger("qq_sender")

def log_send_event(event):
    logger.info(f"Sent event: {event}")
```

---

## Testing Patterns

- **Test Framework:** Unknown (likely `pytest` or similar, based on file naming)
- **Test File Pattern:** All test files are named with `test_*.py` (e.g., `test_qq_sender.py`, `test_client_session_schema.py`).
- **Test Structure:** Place tests under the `tests/` directory, matching the module being tested.
- **Example Test:**
    ```python
    # tests/test_qq_sender.py
    from core.senders.qq import send_messages

    def test_send_messages_batches():
        messages = [...]
        result = send_messages(messages)
        assert result is not None
    ```

---

## Commands

| Command              | Purpose                                               |
|----------------------|-------------------------------------------------------|
| /update-qq-sender    | Add, fix, or enhance QQ sender features or bugs       |
| /refactor-qq-sender  | Modularize or refactor QQ sender logic                |
| /fix-session-forwarder | Improve session or forwarding reliability           |
| /add-qq-logging      | Add or improve QQ sender logging and diagnostics      |
```
