from __future__ import annotations

import asyncio
import copy
import inspect
import time
from typing import Any

from astrbot.api import logger

from ..common.text_tools import normalize_telegram_channel_name


class TGChannelCache:
    def __init__(
        self,
        plugin: Any,
        ttl_seconds: int = 3600,
        *,
        # 给足扫描上限；真正截止靠 refresh_timeout=120s。
        max_dialogs: int = 2000,
        refresh_timeout: float = 120.0,
        failure_cooldown: float = 20.0,
    ):
        self.plugin = plugin
        self.ttl_seconds = ttl_seconds
        self.max_dialogs = max(1, int(max_dialogs))
        self.refresh_timeout = max(1.0, float(refresh_timeout))
        self.failure_cooldown = max(0.5, float(failure_cooldown))
        self._lock = asyncio.Lock()
        self._last_refresh_at = 0.0
        self._last_failure_at = 0.0
        self._channels: list[dict[str, Any]] = []
        self._available = False
        self._message = "Telegram client is unavailable."
        self._pending_configured_refs: list[str] = []

    async def list_channels(
        self,
        configured_channel_refs: list[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        configured = [
            normalize_telegram_channel_name(str(raw or ""))
            for raw in (configured_channel_refs or [])
        ]
        configured = [item for item in configured if item]
        self._pending_configured_refs = configured

        if force or not self._is_fresh():
            await self._refresh(force=force, configured_channel_refs=configured)
        channels = self._merge_configured_channels(configured)
        return {
            "channels": channels,
            "available": self._available,
            "message": self._message,
        }

    def _is_fresh(self) -> bool:
        now = time.time()
        if self._available and self._channels:
            return (now - self._last_refresh_at) < self.ttl_seconds
        # 失败/空结果：短冷却，避免 90s 内卡死在“加载失败”
        anchor = self._last_failure_at or self._last_refresh_at
        if anchor <= 0:
            return False
        return (now - anchor) < self.failure_cooldown

    async def _refresh(
        self,
        *,
        force: bool = False,
        configured_channel_refs: list[str] | None = None,
    ) -> None:
        async with self._lock:
            if not force and self._is_fresh() and (self._channels or self._last_failure_at):
                # 冷却期内直接返回；仍允许 merge configured。
                return

            client = getattr(
                getattr(self.plugin, "client_wrapper", None), "client", None
            )
            if client is None:
                self._set_unavailable("Telegram client is unavailable.")
                return

            if not await self._is_client_connected(client):
                self._set_unavailable("Telegram client is disconnected.")
                return

            if not await self._is_client_authorized(client):
                self._set_unavailable("Telegram client is not authorized.")
                return

            configured = configured_channel_refs or self._pending_configured_refs or []
            channels_by_ref: dict[str, dict[str, Any]] = {}
            started_at = time.time()

            # 1) 先解析已配置频道：快、且对选择器最有用；不依赖全量对话框。
            resolve_budget = min(30.0, max(5.0, self.refresh_timeout * 0.25))
            resolved = await self._resolve_configured_entities(
                client, configured, timeout=resolve_budget
            )
            channels_by_ref.update(resolved)

            # 2) 再扫对话框补全；总时长不超过 refresh_timeout，避免叠成 150s。
            remaining = max(1.0, self.refresh_timeout - (time.time() - started_at))
            try:
                dialogs = await asyncio.wait_for(
                    self._load_dialogs(client),
                    timeout=remaining,
                )
                for dialog in dialogs:
                    entity = getattr(dialog, "entity", dialog)
                    if not self._is_channel_like(dialog, entity):
                        continue
                    channel = self._normalize_channel(dialog, entity)
                    channel_ref = channel["channel_ref"]
                    if not channel_ref:
                        continue
                    # live 结果应覆盖 resolved 占位；仅保留已经写入的 live 结果。
                    existing = channels_by_ref.get(channel_ref)
                    if existing and existing.get("source") == "live":
                        continue
                    channels_by_ref[channel_ref] = channel
            except asyncio.TimeoutError:
                logger.warning(
                    "[WebAdmin] Telegram channel dialog scan timed out after %.1fs",
                    remaining,
                )
            except Exception as exc:
                logger.warning("[WebAdmin] Failed to load Telegram channels: %s", exc)

            if channels_by_ref:
                self._channels = self._sort_channels(channels_by_ref.values())
                self._available = True
                self._message = ""
                self._last_refresh_at = time.time()
                self._last_failure_at = 0.0
                return

            # 全失败：短冷却，前端可稍后重试；仍只展示 configured 占位。
            self._channels = []
            self._available = False
            self._message = "Telegram 频道列表加载超时，可稍后手动刷新。"
            self._last_failure_at = time.time()
            self._last_refresh_at = self._last_failure_at

    async def _resolve_configured_entities(
        self,
        client: Any,
        configured_channel_refs: list[str],
        *,
        timeout: float,
    ) -> dict[str, dict[str, Any]]:
        if not configured_channel_refs or not hasattr(client, "get_entity"):
            return {}

        async def one(ref: str) -> tuple[str, dict[str, Any] | None]:
            try:
                entity = await asyncio.wait_for(client.get_entity(ref), timeout=10.0)
            except Exception as exc:
                logger.debug("[WebAdmin] resolve configured channel %s failed: %s", ref, exc)
                return ref, None
            dialog = SimpleDialog(entity)
            if not self._is_channel_like(dialog, entity):
                logger.debug("[WebAdmin] configured Telegram ref is not a channel: %s", ref)
                return ref, None
            channel = self._normalize_channel(dialog, entity)
            channel["source"] = "resolved"
            # 保持用户配置的 channel_ref，避免 username/id 形态漂移
            channel["channel_ref"] = ref
            if not channel.get("username") and not ref.lstrip("-").isdigit():
                channel["username"] = ref.lstrip("@")
            return ref, channel

        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(one(ref) for ref in configured_channel_refs),
                    return_exceptions=True,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[WebAdmin] configured channel resolve timed out after %.1fs", timeout)
            return {}
        except Exception as exc:
            logger.debug("[WebAdmin] configured channel resolve failed: %s", exc)
            return {}

        out: dict[str, dict[str, Any]] = {}
        for item in results:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            ref, channel = item
            if channel:
                out[str(ref)] = channel
        return out

    def _set_unavailable(self, message: str) -> None:
        self._channels = []
        self._available = False
        self._message = message
        now = time.time()
        self._last_failure_at = now
        self._last_refresh_at = now

    def invalidate(self) -> None:
        """清空缓存与新鲜度标记，下次 list 必重新拉取。"""
        self._channels = []
        self._available = False
        self._message = "Telegram channel cache invalidated."
        self._last_refresh_at = 0.0
        self._last_failure_at = 0.0

    async def _is_client_connected(self, client: Any) -> bool:
        checker = getattr(client, "is_connected", None)
        if checker is None:
            return True
        try:
            result = checker() if callable(checker) else checker
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def _is_client_authorized(self, client: Any) -> bool:
        wrapper = getattr(self.plugin, "client_wrapper", None)
        checker = getattr(wrapper, "is_authorized", None)
        if checker is not None:
            try:
                result = checker() if callable(checker) else checker
                if inspect.isawaitable(result):
                    result = await result
                if result:
                    return True
            except Exception:
                pass

        client_checker = getattr(client, "is_user_authorized", None)
        if client_checker is None:
            return True
        try:
            result = client_checker() if callable(client_checker) else client_checker
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def _load_dialogs(self, client: Any) -> list[Any]:
        # 优先 get_dialogs(limit=N)：比无界 iter 更快结束，也更不容易和下载任务抢连接。
        if hasattr(client, "get_dialogs"):
            try:
                result = client.get_dialogs(limit=self.max_dialogs)
            except TypeError:
                # 仅对“不接受 limit”的签名回退；await 中的 TypeError 不重试。
                result = client.get_dialogs()
            if inspect.isawaitable(result):
                result = await result
            return list(result or [])[: self.max_dialogs]

        if hasattr(client, "iter_dialogs"):
            dialogs: list[Any] = []
            iterator = client.iter_dialogs()
            async for dialog in iterator:
                dialogs.append(dialog)
                if len(dialogs) >= self.max_dialogs:
                    break
            return dialogs

        return []

    @staticmethod
    def _is_channel_like(dialog: Any, entity: Any) -> bool:
        if bool(getattr(dialog, "is_user", False)):
            return False
        class_name = entity.__class__.__name__.lower()
        if "user" in class_name:
            return False
        if bool(getattr(dialog, "is_channel", False)):
            return True
        if "channel" in class_name:
            return True
        return bool(getattr(entity, "broadcast", False)) or bool(
            getattr(entity, "megagroup", False)
        )

    @classmethod
    def _normalize_channel(cls, dialog: Any, entity: Any) -> dict[str, Any]:
        entity_id = str(getattr(entity, "id", "") or "").strip()
        username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
        title = (
            str(getattr(dialog, "title", "") or "").strip()
            or str(getattr(entity, "title", "") or "").strip()
            or username
            or entity_id
        )
        kind = "supergroup" if bool(getattr(entity, "megagroup", False)) else "channel"
        channel_ref = username or cls._private_channel_ref(entity_id)
        return {
            "id": entity_id,
            "title": title,
            "username": username,
            "channel_ref": channel_ref,
            "kind": kind,
            "source": "live",
            "member_count": cls._safe_optional_int(
                getattr(entity, "participants_count", None)
            ),
        }

    @staticmethod
    def _private_channel_ref(entity_id: str) -> str:
        if not entity_id:
            return ""
        normalized = entity_id.lstrip("-")
        if normalized.startswith("100"):
            return f"-{normalized}"
        return f"-100{normalized}"

    def _merge_configured_channels(
        self, configured_channel_refs: list[str]
    ) -> list[dict[str, Any]]:
        channels_by_ref = {
            item["channel_ref"]: copy.deepcopy(item) for item in self._channels
        }
        for raw_ref in configured_channel_refs:
            channel_ref = normalize_telegram_channel_name(str(raw_ref or ""))
            if not channel_ref or channel_ref in channels_by_ref:
                continue
            channels_by_ref[channel_ref] = {
                "id": channel_ref if channel_ref.lstrip("-").isdigit() else "",
                "title": f"@{channel_ref}"
                if not channel_ref.lstrip("-").isdigit()
                else channel_ref,
                "username": ""
                if channel_ref.lstrip("-").isdigit()
                else channel_ref.lstrip("@"),
                "channel_ref": channel_ref,
                "kind": "channel",
                "source": "configured",
                "member_count": None,
            }
        return self._sort_channels(channels_by_ref.values())

    @staticmethod
    def _safe_optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _sort_channels(channels: Any) -> list[dict[str, Any]]:
        source_rank = {"live": 0, "resolved": 1, "configured": 2}
        return sorted(
            [copy.deepcopy(item) for item in channels],
            key=lambda item: (
                source_rank.get(str(item.get("source", "")), 9),
                str(item.get("title", "")).lower(),
                str(item.get("channel_ref", "")).lower(),
            ),
        )


class SimpleDialog:
    """Minimal dialog-like object for entity-only normalization."""

    def __init__(self, entity: Any):
        self.entity = entity
        self.title = getattr(entity, "title", None)
        class_name = entity.__class__.__name__.lower()
        self.is_user = bool(getattr(entity, "is_user", False)) or "user" in class_name
        self.is_channel = not self.is_user and (
            bool(getattr(entity, "is_channel", False))
            or "channel" in class_name
            or bool(getattr(entity, "broadcast", False))
            or bool(getattr(entity, "megagroup", False))
        )
