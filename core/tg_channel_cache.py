from __future__ import annotations

import asyncio
import copy
import inspect
import time
from typing import Any

from astrbot.api import logger

from ..common.text_tools import normalize_telegram_channel_name


class TGChannelCache:
    def __init__(self, plugin: Any, ttl_seconds: int = 90):
        self.plugin = plugin
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._last_refresh_at = 0.0
        self._channels: list[dict[str, Any]] = []
        self._available = False
        self._message = "Telegram client is unavailable."

    async def list_channels(
        self,
        configured_channel_refs: list[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if force or not self._is_fresh():
            await self._refresh(force=force)
        channels = self._merge_configured_channels(configured_channel_refs or [])
        return {
            "channels": channels,
            "available": self._available,
            "message": self._message,
        }

    def _is_fresh(self) -> bool:
        return (time.time() - self._last_refresh_at) < self.ttl_seconds

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and self._is_fresh() and self._channels:
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

            channels_by_ref: dict[str, dict[str, Any]] = {}
            try:
                dialogs = await self._load_dialogs(client)
            except Exception as exc:
                logger.warning("[WebAdmin] Failed to load Telegram channels: %s", exc)
                self._channels = []
                self._available = False
                self._message = f"Failed to load Telegram channels: {exc}"
                self._last_refresh_at = time.time()
                return

            for dialog in dialogs:
                entity = getattr(dialog, "entity", dialog)
                if not self._is_channel_like(dialog, entity):
                    continue
                channel = self._normalize_channel(dialog, entity)
                channel_ref = channel["channel_ref"]
                if not channel_ref or channel_ref in channels_by_ref:
                    continue
                channels_by_ref[channel_ref] = channel

            self._channels = self._sort_channels(channels_by_ref.values())
            self._available = True
            self._message = ""
            self._last_refresh_at = time.time()

    def _set_unavailable(self, message: str) -> None:
        self._channels = []
        self._available = False
        self._message = message
        self._last_refresh_at = time.time()

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
        if hasattr(client, "iter_dialogs"):
            dialogs: list[Any] = []
            iterator = client.iter_dialogs()
            async for dialog in iterator:
                dialogs.append(dialog)
            return dialogs

        if hasattr(client, "get_dialogs"):
            result = client.get_dialogs()
            if inspect.isawaitable(result):
                result = await result
            return list(result or [])

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
        return sorted(
            [copy.deepcopy(item) for item in channels],
            key=lambda item: (
                str(item.get("source", "")) != "live",
                str(item.get("title", "")).lower(),
                str(item.get("channel_ref", "")).lower(),
            ),
        )
