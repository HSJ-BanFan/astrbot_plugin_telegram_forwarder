from __future__ import annotations

import asyncio
from typing import Any

try:
    from .senders.qq_runtime import get_platform_bot, get_platform_instances
except Exception:  # pragma: no cover
    get_platform_bot = None
    get_platform_instances = None

try:  # pragma: no cover - AstrBot adapter may be unavailable in unit tests
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
except Exception:  # pragma: no cover - import guard for non-AstrBot test runtime
    AiocqhttpAdapter = None

from .platform_directory import (
    DirectoryAdapter,
    PlatformDirectory,
    QQDirectoryAdapter,
)


class QQGroupCache:
    """Delegating facade for QQ group discovery and caching backed by PlatformDirectory."""

    def __init__(
        self,
        plugin: Any,
        ttl_seconds: int = 3600,
        *,
        failure_cooldown: float = 20.0,
        adapter: DirectoryAdapter | None = None,
    ):
        self.plugin = plugin
        self.adapter = adapter or QQDirectoryAdapter(
            plugin,
            get_platform_bot=get_platform_bot,
            get_platform_instances=get_platform_instances,
            iter_platforms_fn=self._iter_qq_platforms,
        )
        self._directory = PlatformDirectory(
            adapter=self.adapter,
            ttl_seconds=ttl_seconds,
            failure_cooldown=failure_cooldown,
            item_key="groups",
            item_name="group list",
            default_unavailable_message="QQ platform is unavailable.",
            default_invalidated_message="QQ group cache invalidated.",
        )

    async def list_groups(
        self,
        configured_group_ids: list[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        result = await self._directory.list_items(configured_group_ids, force=force)
        return {
            "groups": result["groups"],
            "available": result["available"],
            "message": result["message"],
        }

    def _is_fresh(self) -> bool:
        return self._directory._is_fresh()

    async def _refresh(self, *, force: bool = False) -> None:
        await self._directory._refresh(force=force)

    def invalidate(self) -> None:
        """清空缓存与新鲜度标记，下次 list 必重新拉取。"""
        self._directory.invalidate()

    async def warm_up(self, *, force: bool = False) -> None:
        await self._directory.warm_up(force=force)

    @property
    def ttl_seconds(self) -> int:
        return self._directory.ttl_seconds

    @ttl_seconds.setter
    def ttl_seconds(self, value: int) -> None:
        self._directory.ttl_seconds = int(value)

    @property
    def failure_cooldown(self) -> float:
        return self._directory.failure_cooldown

    @failure_cooldown.setter
    def failure_cooldown(self, value: float) -> None:
        self._directory.failure_cooldown = max(0.5, float(value))

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
    def _groups(self) -> list[dict[str, Any]]:
        return self._directory._items

    @_groups.setter
    def _groups(self, value: list[dict[str, Any]]) -> None:
        self._directory._items = value

    @property
    def _available(self) -> bool:
        return self._directory._available

    @_available.setter
    def _available(self, value: bool) -> None:
        self._directory._available = bool(value)

    @property
    def _message(self) -> str:
        return self._directory._message

    @_message.setter
    def _message(self, value: str) -> None:
        self._directory._message = str(value)

    def _iter_qq_platforms(self) -> list[tuple[Any, str]]:
        platforms = (
            get_platform_instances(getattr(self.plugin, "context", None))
            if get_platform_instances
            else []
        )
        adapter_matches: list[tuple[Any, str]] = []
        duck_matches: list[tuple[Any, str]] = []
        for platform in platforms:
            try:
                meta = platform.meta()
                platform_id = str(getattr(meta, "id", "") or "").strip()
                platform_name = str(getattr(meta, "name", "") or "").lower()
            except Exception:
                platform_id = str(getattr(platform, "id", "") or "").strip()
                platform_name = str(getattr(platform, "name", "") or "").lower()
            if not platform_id:
                continue
            if AiocqhttpAdapter is not None and isinstance(platform, AiocqhttpAdapter):
                adapter_matches.append((platform, platform_id))
                continue
            if platform_name and not any(
                marker in platform_name for marker in ("aiocqhttp", "qq", "onebot")
            ):
                continue
            duck_matches.append((platform, platform_id))
        return adapter_matches + duck_matches

    def _merge_configured_groups(
        self, configured_group_ids: list[str]
    ) -> list[dict[str, Any]]:
        return self._directory._merge_configured_items(configured_group_ids)

    @staticmethod
    def _mark_cached(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for group in groups:
            if group.get("source") == "live":
                group["source"] = "cached"
        return groups

    @staticmethod
    def _extract_group_list(result: Any) -> list[dict[str, Any]]:
        return QQDirectoryAdapter._extract_group_list(result)

    @classmethod
    def _normalize_group(
        cls, raw_group: dict[str, Any], platform_id: str
    ) -> dict[str, Any]:
        return QQDirectoryAdapter._normalize_group(raw_group, platform_id)

    @classmethod
    def _fallback_group(cls, group_id: str) -> dict[str, Any]:
        return QQDirectoryAdapter._fallback_group(group_id)

    @staticmethod
    def _avatar_url(group_id: str) -> str:
        return QQDirectoryAdapter._avatar_url(group_id)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        return QQDirectoryAdapter._safe_int(value, default)

    @staticmethod
    def _sort_groups(groups: Any) -> list[dict[str, Any]]:
        return QQDirectoryAdapter._sort_groups(groups)
