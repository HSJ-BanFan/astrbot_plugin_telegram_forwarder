"""转发后定时自动撤回。

在消息成功发送后记录平台 message_id，并在配置的窗口到期后调用平台删除接口。
设计目标：
- 不改变现有发送成功/失败语义
- 撤回失败只记日志，不影响主发送链路
- 队列可持久化，插件重载后仍可继续撤回未到期项
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

QQ_DELETE_ACTIONS = ("delete_msg", "delete_message")


def _positive_int(value: object, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    return parsed


def extract_message_ids(result: object) -> list[int]:
    """从 OneBot / aiocqhttp 返回值中尽量提取 message_id 列表。"""
    ids: list[int] = []

    def add_one(value: object) -> None:
        if value is None or isinstance(value, bool):
            return
        try:
            message_id = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if message_id > 0 and message_id not in ids:
            ids.append(message_id)

    if result is None:
        return []

    if isinstance(result, (int, float, str)):
        add_one(result)
        return ids

    if isinstance(result, Mapping):
        data = result.get("data")
        if isinstance(data, Mapping):
            add_one(data.get("message_id"))
            raw_ids = data.get("message_ids")
            if isinstance(raw_ids, Iterable) and not isinstance(
                raw_ids, (str, bytes, bytearray)
            ):
                for item in raw_ids:
                    add_one(item)
        add_one(result.get("message_id"))
        raw_ids = result.get("message_ids")
        if isinstance(raw_ids, Iterable) and not isinstance(
            raw_ids, (str, bytes, bytearray)
        ):
            for item in raw_ids:
                add_one(item)
        return ids

    add_one(getattr(result, "message_id", None))
    return ids


@asynccontextmanager
async def capture_bot_message_ids(bot: object):
    """临时包装 bot 发送相关方法，捕获返回的 message_id。

    AstrBot 的 `context.send_message` 只返回 bool，不透传 OneBot 回执。
    通过在发送期间包装 bot 的 `send_group_msg` / `send_private_msg` / `call_action`，
    可以在不重写消息序列化逻辑的前提下拿到平台 message_id。
    """

    captured: list[int] = []
    if bot is None:
        yield captured
        return

    originals: dict[str, Any] = {}

    def wrap_async(method_name: str, original: Callable[..., Awaitable[Any]]):
        async def wrapped(*args: Any, **kwargs: Any):
            result = await original(*args, **kwargs)
            for message_id in extract_message_ids(result):
                if message_id not in captured:
                    captured.append(message_id)
            return result

        return wrapped

    for method_name in ("send_group_msg", "send_private_msg", "call_action", "send"):
        original = getattr(bot, method_name, None)
        if original is None or not callable(original):
            continue
        # 只包装 async 方法，避免破坏同步接口。
        is_async = asyncio.iscoroutinefunction(original) or asyncio.iscoroutinefunction(
            getattr(original, "__func__", None)
        )
        if not is_async:
            unbound = getattr(type(bot), method_name, None)
            is_async = asyncio.iscoroutinefunction(unbound)
        if not is_async:
            continue
        originals[method_name] = original
        try:
            setattr(bot, method_name, wrap_async(method_name, original))
        except Exception:
            originals.pop(method_name, None)

    try:
        yield captured
    finally:
        for method_name, original in originals.items():
            with suppress(Exception):
                setattr(bot, method_name, original)


@dataclass
class RecallItem:
    """一条待撤回记录。"""

    platform: str  # "qq" | "telegram"
    message_ids: list[int] = field(default_factory=list)
    target: str = ""
    recall_at: float = 0.0
    created_at: float = 0.0
    source_channel: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "message_ids": [int(x) for x in self.message_ids if int(x) > 0],
            "target": str(self.target or ""),
            "recall_at": float(self.recall_at or 0),
            "created_at": float(self.created_at or 0),
            "source_channel": str(self.source_channel or ""),
            "note": str(self.note or ""),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RecallItem | None:
        platform = str(raw.get("platform") or "").strip().lower()
        if platform not in {"qq", "telegram"}:
            return None
        message_ids: list[int] = []
        raw_ids = raw.get("message_ids") or []
        if isinstance(raw_ids, Iterable) and not isinstance(
            raw_ids, (str, bytes, bytearray)
        ):
            for item in raw_ids:
                try:
                    value = int(item)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    message_ids.append(value)
        if not message_ids:
            return None
        return cls(
            platform=platform,
            message_ids=message_ids,
            target=str(raw.get("target") or ""),
            recall_at=float(raw.get("recall_at") or 0),
            created_at=float(raw.get("created_at") or 0),
            source_channel=str(raw.get("source_channel") or ""),
            note=str(raw.get("note") or ""),
        )


class AutoRecallManager:
    """管理自动撤回队列并执行到期删除。"""

    def __init__(
        self,
        *,
        config: Mapping[str, object] | None = None,
        storage: Any | None = None,
        get_qq_bot: Callable[[], object | None] | None = None,
        get_tg_client: Callable[[], object | None] | None = None,
    ):
        self.config = config or {}
        self.storage = storage
        self.get_qq_bot = get_qq_bot or (lambda: None)
        self.get_tg_client = get_tg_client or (lambda: None)
        self._lock = asyncio.Lock()
        self._queue: list[RecallItem] = []
        self._loaded = False

    def reload_config(self, config: Mapping[str, object] | None) -> None:
        self.config = config or {}

    def _forward_cfg(self) -> Mapping[str, object]:
        cfg = self.config.get("forward_config", {}) if self.config else {}
        return cfg if isinstance(cfg, Mapping) else {}

    def is_enabled(self) -> bool:
        return bool(self._forward_cfg().get("auto_recall_enabled", False))

    def window_seconds(self) -> int:
        return _positive_int(
            self._forward_cfg().get("auto_recall_window_seconds", 120),
            120,
            minimum=1,
        )

    def max_queue_size(self) -> int:
        return _positive_int(
            self._forward_cfg().get("auto_recall_max_queue_size", 500),
            500,
            minimum=1,
        )

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.storage is None or not hasattr(self.storage, "get_auto_recall_queue"):
            return
        try:
            raw_items = self.storage.get_auto_recall_queue()
        except Exception as exc:
            logger.warning(f"[AutoRecall] 加载撤回队列失败: {exc}")
            return
        restored: list[RecallItem] = []
        if isinstance(raw_items, list):
            for raw in raw_items:
                if not isinstance(raw, Mapping):
                    continue
                item = RecallItem.from_dict(raw)
                if item is not None:
                    restored.append(item)
        self._queue = restored

    def _persist(self) -> None:
        if self.storage is None or not hasattr(self.storage, "set_auto_recall_queue"):
            return
        try:
            self.storage.set_auto_recall_queue([item.to_dict() for item in self._queue])
        except Exception as exc:
            logger.warning(f"[AutoRecall] 持久化撤回队列失败: {exc}")

    def schedule(
        self,
        *,
        platform: str,
        message_ids: Iterable[int],
        target: str = "",
        source_channel: str = "",
        note: str = "",
        window_seconds: int | None = None,
        now_ts: float | None = None,
    ) -> RecallItem | None:
        """登记一条待撤回记录。未启用或无有效 message_id 时返回 None。"""
        if not self.is_enabled():
            return None

        normalized_ids: list[int] = []
        for raw_id in message_ids:
            try:
                value = int(raw_id)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in normalized_ids:
                normalized_ids.append(value)
        if not normalized_ids:
            return None

        platform_name = str(platform or "").strip().lower()
        if platform_name not in {"qq", "telegram"}:
            return None

        self._ensure_loaded()
        now = time.time() if now_ts is None else float(now_ts)
        delay = (
            self.window_seconds()
            if window_seconds is None
            else _positive_int(window_seconds, self.window_seconds(), minimum=1)
        )
        item = RecallItem(
            platform=platform_name,
            message_ids=normalized_ids,
            target=str(target or ""),
            recall_at=now + delay,
            created_at=now,
            source_channel=str(source_channel or ""),
            note=str(note or ""),
        )
        self._queue.append(item)
        overflow = len(self._queue) - self.max_queue_size()
        if overflow > 0:
            dropped = self._queue[:overflow]
            self._queue = self._queue[overflow:]
            logger.warning(f"[AutoRecall] 撤回队列超限，丢弃最早 {len(dropped)} 条记录")
        self._persist()
        logger.info(
            f"[AutoRecall] 已登记 {platform_name} 撤回: ids={normalized_ids}, "
            f"target={target!r}, after={delay}s, source={source_channel or '-'}"
        )
        return item

    def pending_count(self) -> int:
        self._ensure_loaded()
        return len(self._queue)

    async def process_due(self, *, now_ts: float | None = None) -> dict[str, int]:
        """处理所有到期记录，返回统计。"""
        if not self.is_enabled():
            # 关闭时仍清理过期项，避免旧队列无限堆积
            self._ensure_loaded()
            if not self._queue:
                return {"due": 0, "success": 0, "failed": 0, "skipped": 0}
            now = time.time() if now_ts is None else float(now_ts)
            kept = [item for item in self._queue if item.recall_at > now]
            removed = len(self._queue) - len(kept)
            if removed:
                self._queue = kept
                self._persist()
            return {"due": removed, "success": 0, "failed": 0, "skipped": removed}

        async with self._lock:
            self._ensure_loaded()
            now = time.time() if now_ts is None else float(now_ts)
            due_items = [item for item in self._queue if item.recall_at <= now]
            if not due_items:
                return {"due": 0, "success": 0, "failed": 0, "skipped": 0}

            success = 0
            failed = 0
            skipped = 0
            remaining: list[RecallItem] = [
                item for item in self._queue if item.recall_at > now
            ]

            for item in due_items:
                try:
                    ok = await self._recall_one(item)
                except Exception as exc:
                    logger.warning(
                        f"[AutoRecall] 撤回异常 platform={item.platform} "
                        f"ids={item.message_ids} target={item.target!r}: {exc}"
                    )
                    ok = False
                if ok:
                    success += 1
                else:
                    # 失败不再无限重试，避免对平台持续打点；保留日志便于排查。
                    failed += 1
                    skipped += 0

            self._queue = remaining
            self._persist()
            if success or failed:
                logger.info(
                    f"[AutoRecall] 处理到期 {len(due_items)} 条: "
                    f"success={success}, failed={failed}"
                )
            return {
                "due": len(due_items),
                "success": success,
                "failed": failed,
                "skipped": skipped,
            }

    async def _recall_one(self, item: RecallItem) -> bool:
        if item.platform == "qq":
            return await self._recall_qq(item)
        if item.platform == "telegram":
            return await self._recall_telegram(item)
        return False

    async def _recall_qq(self, item: RecallItem) -> bool:
        bot = self.get_qq_bot()
        if bot is None:
            logger.warning(
                f"[AutoRecall] QQ bot 不可用，放弃撤回 ids={item.message_ids}"
            )
            return False

        any_success = False
        for message_id in item.message_ids:
            deleted = False
            last_error: Exception | None = None
            for action in QQ_DELETE_ACTIONS:
                try:
                    if hasattr(bot, "call_action"):
                        await bot.call_action(action, message_id=message_id)
                        deleted = True
                        break
                    method = getattr(bot, action, None)
                    if callable(method):
                        result = method(message_id=message_id)
                        if asyncio.iscoroutine(result):
                            await result
                        deleted = True
                        break
                except Exception as exc:
                    last_error = exc
                    continue
            if deleted:
                any_success = True
                logger.info(
                    f"[AutoRecall] QQ 已撤回 message_id={message_id} "
                    f"target={item.target!r} source={item.source_channel or '-'}"
                )
            else:
                logger.warning(
                    f"[AutoRecall] QQ 撤回失败 message_id={message_id} "
                    f"target={item.target!r}: {last_error}"
                )
        return any_success

    async def _recall_telegram(self, item: RecallItem) -> bool:
        client = self.get_tg_client()
        if client is None:
            logger.warning(
                f"[AutoRecall] Telegram client 不可用，放弃撤回 ids={item.message_ids}"
            )
            return False
        target = item.target
        if not target:
            logger.warning(
                f"[AutoRecall] Telegram 目标为空，放弃撤回 ids={item.message_ids}"
            )
            return False

        entity: object = target
        if isinstance(target, str):
            raw = target.strip()
            if raw.startswith("-") or raw.isdigit():
                with suppress(ValueError):
                    entity = int(raw)

        try:
            await client.delete_messages(entity, item.message_ids)
            logger.info(
                f"[AutoRecall] TG 已撤回 ids={item.message_ids} target={target!r} "
                f"source={item.source_channel or '-'}"
            )
            return True
        except Exception as exc:
            logger.warning(
                f"[AutoRecall] TG 撤回失败 ids={item.message_ids} "
                f"target={target!r}: {exc}"
            )
            return False
