"""受控的发送后自动撤回任务生命周期。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from astrbot.api import logger


@dataclass(frozen=True, slots=True)
class SendReceipt:
    """一次真实平台发送的可撤回凭据。"""

    platform: str
    target_session: str
    message_id: str
    source_channel: str
    sent_at: datetime
    kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", str(self.message_id))


@dataclass(frozen=True, slots=True)
class RecallContext:
    """Stable metadata shared by all receipts from one sender."""

    platform: str
    target_session: str
    source_channel: str
    kind: str


class TerminalRecallError(Exception):
    """撤回已经没有必要重试的终态错误。"""


class TransientRecallError(Exception):
    """撤回失败可由受控策略重试的瞬态错误。"""


def is_terminal_recall_error(error: Exception) -> bool:
    """判断平台错误是否表示无需再次尝试撤回。"""
    rendered = f"{type(error).__name__} {error}".lower()
    return any(
        marker in rendered
        for marker in (
            "permission",
            "forbidden",
            "admin required",
            "admin privileges",
            "administrator",
            "not found",
            "not exist",
            "already deleted",
            "message deleted",
            "messageidinvaliderror",
            "message id invalid",
            "peeridinvaliderror",
            "peer id invalid",
            "chatadminrequirederror",
            "channelprivateerror",
            "userbannedinchannelerror",
            "recall failed",
            "权限",
            "不存在",
            "已删除",
            "撤回失败",
            "映射",
        )
    )


def is_transient_recall_error(error: Exception) -> bool:
    """判断异常是否表示网络抖动、超时或服务暂时不可用。"""
    rendered = f"{type(error).__name__} {error}".lower()
    return any(
        marker in rendered
        for marker in (
            "timeout",
            "timed out",
            "time out",
            "connection",
            "network",
            "temporarily unavailable",
            "temporary failure",
            "rate limit",
            "too many requests",
            "floodwait",
            "flood wait",
            "websocket",
            "transport",
            "网络",
            "连接",
            "超时",
        )
    )


def classify_recall_error(error: Exception) -> Exception:
    """Map known platform failures to the retryable or terminal contract."""
    if isinstance(error, (TransientRecallError, TerminalRecallError)):
        return error
    if is_terminal_recall_error(error):
        return TerminalRecallError(str(error))
    if isinstance(
        error, (TimeoutError, ConnectionError, OSError)
    ) or is_transient_recall_error(error):
        return TransientRecallError(str(error))
    return error


RecallFn = Callable[[SendReceipt], Awaitable[None]]

RECALL_RETRY_DELAY_SECONDS = 1.0


class RecallRegistry:
    """持有自动撤回任务强引用, 并负责统一取消与异常隔离。"""

    @classmethod
    def from_config(cls, config: object) -> RecallRegistry:
        auto_recall = get_auto_recall_config(config)
        return cls(
            max_pending=auto_recall.get("max_pending", 1000),
            on_error=auto_recall.get("on_error", "log"),
        )

    def __init__(self, max_pending: int = 1000, on_error: str = "log") -> None:
        self.tasks: set[asyncio.Task] = set()
        self._kept_receipts: list[SendReceipt] = []
        self._closed = False
        self.reconfigure(max_pending=max_pending, on_error=on_error)

    def reconfigure(
        self, *, max_pending: object = 1000, on_error: object = "log"
    ) -> None:
        """Update runtime policies, preserving tasks already owned by the registry."""
        try:
            resolved_max_pending = int(max_pending)
        except (TypeError, ValueError, OverflowError):
            resolved_max_pending = 1000
        self.max_pending = max(0, resolved_max_pending)
        self.on_error = (
            on_error
            if isinstance(on_error, str) and on_error in {"log", "retry_once", "keep"}
            else "log"
        )

    def reconfigure_from_config(self, config: object) -> None:
        """Apply the auto-recall policies from a refreshed plugin configuration."""
        auto_recall = get_auto_recall_config(config)
        self.reconfigure(
            max_pending=auto_recall.get("max_pending", 1000),
            on_error=auto_recall.get("on_error", "log"),
        )

    @property
    def pending_count(self) -> int:
        """当前仍由注册表持有的撤回任务数。"""
        return len(self.tasks)

    @property
    def kept_receipts(self) -> tuple[SendReceipt, ...]:
        """已按 ``keep`` 策略保留、等待后续人工处理的撤回凭据。"""
        return tuple(self._kept_receipts)

    def _keep_receipt(self, receipt: SendReceipt, error: Exception) -> None:
        if receipt not in self._kept_receipts:
            self._kept_receipts.append(receipt)
        logger.warning(
            "[RecallRegistry] recall failed; keeping message and receipt "
            "without retry platform=%s target=%s message_id=%s: %s",
            receipt.platform,
            receipt.target_session,
            receipt.message_id,
            error,
        )

    def schedule(
        self,
        delay: float,
        recall_fn: RecallFn,
        receipt: SendReceipt,
    ) -> asyncio.Task | None:
        """安排一次撤回; 达到容量或关闭后返回 ``None``。"""
        if self._closed:
            logger.debug(
                "[RecallRegistry] registry is closed; receipt was not scheduled"
            )
            return None
        if len(self.tasks) >= self.max_pending:
            logger.warning(
                "[RecallRegistry] pending limit reached (%s); receipt was not scheduled",
                self.max_pending,
            )
            return None

        task = asyncio.create_task(self._run(delay, recall_fn, receipt))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _run(
        self, delay: float, recall_fn: RecallFn, receipt: SendReceipt
    ) -> None:
        try:
            resolved_delay = float(delay)
        except (TypeError, ValueError, OverflowError):
            resolved_delay = 0.0
        await asyncio.sleep(
            max(0.0, resolved_delay) if math.isfinite(resolved_delay) else 0.0
        )

        retried = False
        while True:
            try:
                await recall_fn(receipt)
                return
            except asyncio.CancelledError:
                raise
            except TerminalRecallError as exc:
                if self.on_error == "keep":
                    self._keep_receipt(receipt, exc)
                    return
                logger.warning(
                    "[RecallRegistry] terminal recall failure platform=%s target=%s "
                    "message_id=%s: %s",
                    receipt.platform,
                    receipt.target_session,
                    receipt.message_id,
                    exc,
                )
                return
            except TransientRecallError as exc:
                if self.on_error == "retry_once" and not retried:
                    retried = True
                    logger.warning(
                        "[RecallRegistry] transient recall failure; retrying once "
                        "platform=%s target=%s message_id=%s: %s",
                        receipt.platform,
                        receipt.target_session,
                        receipt.message_id,
                        exc,
                    )
                    await asyncio.sleep(RECALL_RETRY_DELAY_SECONDS)
                    continue
                if self.on_error == "keep":
                    self._keep_receipt(receipt, exc)
                    return
                logger.warning(
                    "[RecallRegistry] transient recall failed platform=%s target=%s "
                    "message_id=%s policy=%s: %s",
                    receipt.platform,
                    receipt.target_session,
                    receipt.message_id,
                    self.on_error,
                    exc,
                )
                return
            except Exception as exc:  # noqa: BLE001
                if self.on_error == "keep":
                    self._keep_receipt(receipt, exc)
                    return
                logger.warning(
                    "[RecallRegistry] non-retryable recall failure platform=%s "
                    "target=%s message_id=%s: %s",
                    receipt.platform,
                    receipt.target_session,
                    receipt.message_id,
                    exc,
                )
                return

    async def close(self) -> None:
        """幂等取消并等待所有未完成撤回任务。"""
        self._closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()


def get_auto_recall_config(config: object) -> Mapping[str, object]:
    """Return the shared auto-recall configuration section."""
    config_get = getattr(config, "get", None)
    forward_cfg = config_get("forward_config", {}) if callable(config_get) else {}
    forward_get = getattr(forward_cfg, "get", None)
    auto_recall = forward_get("auto_recall", {}) if callable(forward_get) else {}
    return auto_recall if isinstance(auto_recall, Mapping) else {}


def resolve_recall_delay(
    auto_recall: Mapping[str, object], key: str, default: float = 120.0
) -> float:
    """Normalize a configured delay without allowing invalid scheduler values."""
    try:
        delay = float(auto_recall.get(key, default))
    except (TypeError, ValueError, OverflowError):
        delay = default
    return max(0.0, delay) if math.isfinite(delay) else default


def _normalize_message_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str):
        value = value.strip()
        if value and value.isdigit() and value.lstrip("0"):
            return value
    return None


def extract_message_ids(result: object) -> list[str]:
    """Extract numeric IDs from OneBot responses or Telethon message objects."""
    if result is None or isinstance(result, bool):
        return []
    if isinstance(result, (list, tuple)):
        return [
            message_id for item in result for message_id in extract_message_ids(item)
        ]
    if isinstance(result, Mapping):
        for key in ("message_id", "id"):
            if key in result:
                message_ids = extract_message_ids(result[key])
                if message_ids:
                    return message_ids
        if "data" in result:
            return extract_message_ids(result["data"])
        return []
    message_id = _normalize_message_id(result)
    if message_id is not None:
        return [message_id]
    for attr_name in ("message_id", "id"):
        message_id = _normalize_message_id(getattr(result, attr_name, None))
        if message_id is not None:
            return [message_id]
    return []


def schedule_recall_receipts(
    registry: RecallRegistry | None,
    config: object,
    message_ids: Iterable[object],
    *,
    context: RecallContext,
    enabled_key: str,
    delay_key: str,
    recall_fn: RecallFn,
) -> None:
    """Create one bounded recall task for every real platform message ID."""
    if registry is None:
        return
    auto_recall = get_auto_recall_config(config)
    if not auto_recall.get(enabled_key, False):
        return
    delay = resolve_recall_delay(auto_recall, delay_key)
    for message_id in message_ids:
        normalized_id = _normalize_message_id(message_id)
        if normalized_id is None:
            continue
        registry.schedule(
            delay,
            recall_fn,
            SendReceipt(
                platform=context.platform,
                target_session=context.target_session,
                message_id=normalized_id,
                source_channel=context.source_channel,
                sent_at=datetime.now(timezone.utc),
                kind=context.kind,
            ),
        )
