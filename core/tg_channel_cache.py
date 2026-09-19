from __future__ import annotations

import asyncio
from typing import Any

try:
    from ..common.text_tools import normalize_telegram_channel_name
except Exception:  # pragma: no cover
    try:
        from astrbot_plugin_telegram_forwarder.common.text_tools import (
            normalize_telegram_channel_name,
        )
    except Exception:  # pragma: no cover
        def normalize_telegram_channel_name(value: Any) -> str:
            return str(value or "").strip().lstrip("@")

from .platform_directory import (
    DirectoryAdapter,
    PlatformDirectory,
    SimpleDialog,
    TGDirectoryAdapter,
)


class TGChannelCache:
    """Delegating facade for Telegram channel discovery and caching backed by PlatformDirectory."""

    def __init__(
        self,
        plugin: Any,
        ttl_seconds: int = 3600,
        *,
        max_dialogs: int = 2000,
        refresh_timeout: float = 120.0,
        failure_cooldown: float = 20.0,
        partial_ttl_seconds: float = 300.0,
        adapter: DirectoryAdapter | None = None,
    ):
        self.plugin = plugin
        self.adapter = adapter or TGDirectoryAdapter(
            plugin,
            max_dialogs=max_dialogs,
            refresh_timeout=refresh_timeout,
        )
        self._directory = PlatformDirectory(
            adapter=self.adapter,
            ttl_seconds=ttl_seconds,
            failure_cooldown=failure_cooldown,
            partial_ttl_seconds=partial_ttl_seconds,
            item_key="channels",
            item_name="channel list",
            default_unavailable_message="Telegram client is unavailable.",
            default_invalidated_message="Telegram channel cache invalidated.",
        )
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

        result = await self._directory.list_items(configured, force=force)
        return {
            "channels": result["channels"],
            "available": result["available"],
            "partial": result["partial"],
            "message": result["message"],
        }

    def _is_fresh(self) -> bool:
        return self._directory._is_fresh()

    async def _refresh(
        self,
        *,
        force: bool = False,
        configured_channel_refs: list[str] | None = None,
    ) -> None:
        configured = configured_channel_refs or self._pending_configured_refs or []
        await self._directory._refresh(force=force, configured_ids=configured)

    def invalidate(self) -> None:
        """清空缓存与新鲜度标记，下次 list 必重新拉取。"""
        self._directory.invalidate()
        self._pending_configured_refs = []

    async def warm_up(self, *, force: bool = False) -> None:
        await self._directory.warm_up(force=force)

    @property
    def ttl_seconds(self) -> int:
        return self._directory.ttl_seconds

    @ttl_seconds.setter
    def ttl_seconds(self, value: int) -> None:
        self._directory.ttl_seconds = int(value)

    @property
    def max_dialogs(self) -> int:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return self.adapter.max_dialogs
        return 2000

    @max_dialogs.setter
    def max_dialogs(self, value: int) -> None:
        if isinstance(self.adapter, TGDirectoryAdapter):
            self.adapter.max_dialogs = max(1, int(value))

    @property
    def refresh_timeout(self) -> float:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return self.adapter.refresh_timeout
        return 120.0

    @refresh_timeout.setter
    def refresh_timeout(self, value: float) -> None:
        if isinstance(self.adapter, TGDirectoryAdapter):
            self.adapter.refresh_timeout = max(1.0, float(value))

    @property
    def failure_cooldown(self) -> float:
        return self._directory.failure_cooldown

    @failure_cooldown.setter
    def failure_cooldown(self, value: float) -> None:
        self._directory.failure_cooldown = max(0.5, float(value))

    @property
    def partial_ttl_seconds(self) -> float:
        return self._directory.partial_ttl_seconds

    @partial_ttl_seconds.setter
    def partial_ttl_seconds(self, value: float) -> None:
        self._directory.partial_ttl_seconds = min(
            float(self._directory.ttl_seconds), float(value)
        )

    @property
    def _lock(self) -> asyncio.Lock:
        return self._directory._lock

    @property
    def _last_refresh_at(self) -> float:
        return self._directory._last_refresh_at

    @_last_refresh_at.setter
    def _last_refresh_at(self, value: float) -> None:
        self._directory._last_refresh_at = float(value)

    @property
    def _last_failure_at(self) -> float:
        return self._directory._last_failure_at

    @_last_failure_at.setter
    def _last_failure_at(self, value: float) -> None:
        self._directory._last_failure_at = float(value)

    @property
    def _channels(self) -> list[dict[str, Any]]:
        return self._directory._items

    @_channels.setter
    def _channels(self, value: list[dict[str, Any]]) -> None:
        self._directory._items = value

    @property
    def _available(self) -> bool:
        return self._directory._available

    @_available.setter
    def _available(self, value: bool) -> None:
        self._directory._available = bool(value)

    @property
    def _partial(self) -> bool:
        return self._directory._partial

    @_partial.setter
    def _partial(self, value: bool) -> None:
        self._directory._partial = bool(value)

    @property
    def _message(self) -> str:
        return self._directory._message

    @_message.setter
    def _message(self, value: str) -> None:
        self._directory._message = str(value)

    # Delegating backward-compatible helpers
    async def _resolve_configured_entities(
        self, client: Any, configured_channel_refs: list[str], *, timeout: float
    ) -> dict[str, dict[str, Any]]:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return await self.adapter._resolve_configured_entities(
                client, configured_channel_refs, timeout=timeout
            )
        return {}

    async def _load_dialogs(self, client: Any) -> list[Any]:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return await self.adapter._load_dialogs(client)
        return []

    @staticmethod
    def _is_channel_like(dialog: Any, entity: Any) -> bool:
        return TGDirectoryAdapter._is_channel_like(dialog, entity)

    @classmethod
    def _normalize_channel(cls, dialog: Any, entity: Any) -> dict[str, Any]:
        return TGDirectoryAdapter._normalize_channel(dialog, entity)

    @staticmethod
    def _private_channel_ref(entity_id: str) -> str:
        return TGDirectoryAdapter._private_channel_ref(entity_id)

    @classmethod
    def _channel_alias_keys(cls, channel: dict[str, Any]) -> list[str]:
        return TGDirectoryAdapter._channel_alias_keys(channel)

    @classmethod
    def _build_alias_index(
        cls, channels_by_ref: dict[str, dict[str, Any]]
    ) -> dict[str, str]:
        return TGDirectoryAdapter._build_alias_index(channels_by_ref)

    def _merge_configured_channels(
        self, configured_channel_refs: list[str]
    ) -> list[dict[str, Any]]:
        return self._directory._merge_configured_items(configured_channel_refs)

    @staticmethod
    def _safe_optional_int(value: Any) -> int | None:
        return TGDirectoryAdapter._safe_optional_int(value)

    @staticmethod
    def _sort_channels(channels: Any) -> list[dict[str, Any]]:
        return TGDirectoryAdapter._sort_channels(channels)

    async def _is_client_connected(self, client: Any) -> bool:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return await self.adapter._is_client_connected(client)
        return True

    async def _is_client_authorized(self, client: Any) -> bool:
        if isinstance(self.adapter, TGDirectoryAdapter):
            return await self.adapter._is_client_authorized(client)
        return True
