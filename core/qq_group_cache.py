from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

from astrbot.api import logger

from .senders.qq_runtime import get_platform_bot, get_platform_instances

try:  # pragma: no cover - AstrBot adapter may be unavailable in unit tests
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
except Exception:  # pragma: no cover - import guard for non-AstrBot test runtime
    AiocqhttpAdapter = None


class QQGroupCache:
    def __init__(self, plugin: Any, ttl_seconds: int = 90):
        self.plugin = plugin
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._last_refresh_at = 0.0
        self._groups: list[dict[str, Any]] = []
        self._available = False
        self._message = "QQ platform is unavailable."

    async def list_groups(
        self,
        configured_group_ids: list[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if force or not self._is_fresh():
            await self._refresh(force=force)
        groups = self._merge_configured_groups(configured_group_ids or [])
        return {
            "groups": groups,
            "available": self._available,
            "message": self._message,
        }

    def _is_fresh(self) -> bool:
        return (time.time() - self._last_refresh_at) < self.ttl_seconds

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and self._is_fresh() and self._groups:
                return

            groups_by_id: dict[str, dict[str, Any]] = {}
            saw_platform = False
            saw_client = False

            for platform, platform_id in self._iter_qq_platforms():
                saw_platform = True
                client = get_platform_bot(platform)
                if client is None or not hasattr(client, "call_action"):
                    continue
                saw_client = True
                try:
                    result = await client.call_action("get_group_list")
                except Exception as exc:
                    logger.warning("[WebAdmin] Failed to load QQ groups: %s", exc)
                    continue
                for raw_group in self._extract_group_list(result):
                    group = self._normalize_group(raw_group, platform_id)
                    group_id = group["group_id"]
                    if not group_id or group_id in groups_by_id:
                        continue
                    groups_by_id[group_id] = group

            self._groups = self._sort_groups(groups_by_id.values())
            self._available = saw_client
            if saw_client:
                self._message = ""
            elif saw_platform:
                self._message = (
                    "QQ platform found, but no callable client is available."
                )
            else:
                self._message = "QQ platform is unavailable."
            self._last_refresh_at = time.time()

    def _iter_qq_platforms(self) -> list[tuple[Any, str]]:
        platforms = get_platform_instances(getattr(self.plugin, "context", None))
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
        groups_by_id = {item["group_id"]: copy.deepcopy(item) for item in self._groups}
        for group_id in configured_group_ids:
            normalized = str(group_id or "").strip()
            if not normalized or not normalized.isdigit() or normalized in groups_by_id:
                continue
            groups_by_id[normalized] = self._fallback_group(normalized)
        return self._sort_groups(groups_by_id.values())

    @staticmethod
    def _extract_group_list(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        return []

    @classmethod
    def _normalize_group(
        cls, raw_group: dict[str, Any], platform_id: str
    ) -> dict[str, Any]:
        group_id = str(raw_group.get("group_id", "") or "").strip()
        return {
            "group_id": group_id,
            "group_name": str(raw_group.get("group_name", "") or "").strip()
            or f"群 {group_id}",
            "avatar": cls._avatar_url(group_id),
            "member_count": cls._safe_int(raw_group.get("member_count"), 0),
            "max_member_count": cls._safe_int(raw_group.get("max_member_count"), 0),
            "source": "live",
            "platform_id": platform_id,
            "session": f"{platform_id}:GroupMessage:{group_id}"
            if platform_id and group_id
            else "",
        }

    @classmethod
    def _fallback_group(cls, group_id: str) -> dict[str, Any]:
        return {
            "group_id": group_id,
            "group_name": f"群 {group_id}",
            "avatar": cls._avatar_url(group_id),
            "member_count": 0,
            "max_member_count": 0,
            "source": "configured",
            "platform_id": "",
            "session": "",
        }

    @staticmethod
    def _avatar_url(group_id: str) -> str:
        return f"https://p.qlogo.cn/gh/{group_id}/{group_id}/640" if group_id else ""

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sort_groups(groups: Any) -> list[dict[str, Any]]:
        return sorted(
            [copy.deepcopy(item) for item in groups],
            key=lambda item: (
                not str(item.get("group_id", "")).isdigit(),
                int(item["group_id"]) if str(item.get("group_id", "")).isdigit() else 0,
                str(item.get("group_name", "")),
            ),
        )
