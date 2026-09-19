from __future__ import annotations

import asyncio
import copy
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("platform_directory")

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

def _get_qq_runtime() -> Any:
    try:
        from .senders import qq_runtime

        return qq_runtime
    except Exception:  # pragma: no cover
        import sys

        return sys.modules.get(
            "astrbot_plugin_telegram_forwarder.core.senders.qq_runtime"
        )


try:  # pragma: no cover - AstrBot adapter may be unavailable in unit tests
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
except Exception:  # pragma: no cover - import guard for non-AstrBot test runtime
    AiocqhttpAdapter = None


@dataclass
class DirectoryFetchResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True
    partial: bool = False
    message: str = ""


@runtime_checkable
class DirectoryAdapter(Protocol):
    async def fetch_items(
        self, configured_ids: list[str] | None = None
    ) -> DirectoryFetchResult:
        ...

    def get_item_id(self, item: dict[str, Any]) -> str:
        ...

    def get_item_aliases(self, item: dict[str, Any]) -> list[str]:
        ...

    def mark_cached(self, item: dict[str, Any]) -> dict[str, Any]:
        ...

    def create_fallback(self, target_id: str) -> dict[str, Any] | None:
        ...

    def sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...

    def normalize_configured_id(self, raw_id: str) -> str:
        ...


class BaseDirectoryAdapter:
    def get_item_aliases(self, item: dict[str, Any]) -> list[str]:
        item_id = self.get_item_id(item)
        return [item_id] if item_id else []

    def mark_cached(self, item: dict[str, Any]) -> dict[str, Any]:
        cloned = copy.deepcopy(item)
        if cloned.get("source") == "live":
            cloned["source"] = "cached"
        return cloned

    def normalize_configured_id(self, raw_id: str) -> str:
        return str(raw_id or "").strip()

    def sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(items)


class PlatformDirectory:
    """Unified platform directory cache managing concurrency, TTL, fallback merging and cooldowns."""

    def __init__(
        self,
        adapter: DirectoryAdapter,
        ttl_seconds: int = 3600,
        *,
        failure_cooldown: float = 20.0,
        partial_ttl_seconds: float = 300.0,
        item_key: str = "items",
        item_name: str = "items",
        default_unavailable_message: str = "Platform is unavailable.",
        default_invalidated_message: str = "Platform directory cache invalidated.",
    ):
        self.adapter = adapter
        self.ttl_seconds = int(ttl_seconds)
        self.failure_cooldown = max(0.5, float(failure_cooldown))
        self.partial_ttl_seconds = min(float(ttl_seconds), float(partial_ttl_seconds))
        self.item_key = item_key
        self.item_name = item_name
        self.default_unavailable_message = default_unavailable_message
        self.default_invalidated_message = default_invalidated_message

        self._lock = asyncio.Lock()
        self._last_refresh_at: float = 0.0
        self._last_failure_at: float = 0.0
        self._items: list[dict[str, Any]] = []
        self._available: bool = False
        self._partial: bool = False
        self._message: str = default_unavailable_message

    def _is_fresh(self) -> bool:
        now = time.time()
        if self._available and self._last_refresh_at > 0 and self._last_failure_at <= 0:
            ttl = self.partial_ttl_seconds if self._partial else self.ttl_seconds
            return (now - self._last_refresh_at) < ttl
        anchor = self._last_failure_at or self._last_refresh_at
        if anchor <= 0:
            return False
        return (now - anchor) < self.failure_cooldown

    async def _refresh(
        self,
        *,
        force: bool = False,
        configured_ids: list[str] | None = None,
    ) -> None:
        async with self._lock:
            if not force and self._is_fresh():
                return

            try:
                result = await self.adapter.fetch_items(configured_ids=configured_ids)
            except Exception as exc:
                logger.warning("[PlatformDirectory] fetch_items exception: %s", exc)
                result = DirectoryFetchResult(
                    items=[],
                    available=False,
                    partial=False,
                    message=f"Platform fetch failed: {exc}",
                )

            now = time.time()
            if result.available:
                self._items = self.adapter.sort_items(result.items)
                self._available = True
                self._partial = result.partial
                self._message = result.message
                self._last_refresh_at = now
                self._last_failure_at = 0.0
            else:
                msg = result.message or self.default_unavailable_message
                self._items = [self.adapter.mark_cached(item) for item in self._items]
                if self._items and "last known" not in msg.lower():
                    msg = (
                        f"{msg} Showing last known {self.item_name}."
                        if msg
                        else f"Showing last known {self.item_name}."
                    )
                self._available = False
                self._partial = False
                self._message = msg
                self._last_failure_at = now
                self._last_refresh_at = now

    async def list_items(
        self,
        configured_ids: list[str] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        normalized_configured: list[str] = []
        if configured_ids:
            for raw_id in configured_ids:
                norm = self.adapter.normalize_configured_id(raw_id)
                if norm and norm not in normalized_configured:
                    normalized_configured.append(norm)

        if force or not self._is_fresh():
            await self._refresh(force=force, configured_ids=normalized_configured)

        merged = self._merge_configured_items(normalized_configured)
        result: dict[str, Any] = {
            "items": merged,
            "available": self._available,
            "partial": self._partial,
            "message": self._message,
        }
        if self.item_key != "items":
            result[self.item_key] = merged
        return result

    def _merge_configured_items(
        self, configured_ids: list[str]
    ) -> list[dict[str, Any]]:
        items_by_id: dict[str, dict[str, Any]] = {}
        known_aliases: dict[str, str] = {}

        for item in self._items:
            item_copy = copy.deepcopy(item)
            item_id = self.adapter.get_item_id(item_copy)
            if item_id:
                items_by_id[item_id] = item_copy
            for alias in self.adapter.get_item_aliases(item_copy):
                if alias:
                    known_aliases.setdefault(alias, item_id)

        for raw_id in configured_ids:
            norm_id = self.adapter.normalize_configured_id(raw_id)
            if not norm_id or norm_id in known_aliases:
                continue
            fallback = self.adapter.create_fallback(norm_id)
            if fallback is None:
                continue
            fallback_copy = copy.deepcopy(fallback)
            fb_id = self.adapter.get_item_id(fallback_copy) or norm_id
            items_by_id[fb_id] = fallback_copy
            for alias in self.adapter.get_item_aliases(fallback_copy):
                if alias:
                    known_aliases.setdefault(alias, fb_id)

        return self.adapter.sort_items(list(items_by_id.values()))

    def invalidate(self) -> None:
        """Clears cached items and resets freshness timestamps."""
        self._items = []
        self._available = False
        self._partial = False
        self._message = self.default_invalidated_message
        self._last_refresh_at = 0.0
        self._last_failure_at = 0.0

    async def warm_up(self, *, force: bool = False) -> None:
        """Preheats cache without needing callers to manage lock or freshness."""
        if force or not self._is_fresh():
            await self._refresh(force=force)


def match_qq_platforms(
    platforms: list[Any] | None,
    aiocqhttp_adapter_cls: Any = None,
) -> list[tuple[Any, str]]:
    """Filters platform instances to matching QQ/OneBot platforms."""
    if not platforms:
        return []
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
        if aiocqhttp_adapter_cls is not None and isinstance(platform, aiocqhttp_adapter_cls):
            adapter_matches.append((platform, platform_id))
            continue
        if platform_name and not any(
            marker in platform_name for marker in ("aiocqhttp", "qq", "onebot")
        ):
            continue
        duck_matches.append((platform, platform_id))
    return adapter_matches + duck_matches


class QQDirectoryAdapter(BaseDirectoryAdapter):
    """Directory adapter for QQ groups using AstrBot OneBot platform."""

    def __init__(
        self,
        plugin: Any,
        *,
        get_platform_bot: Any = None,
        get_platform_instances: Any = None,
        iter_platforms_fn: Any = None,
    ):
        self.plugin = plugin
        self._get_platform_bot = get_platform_bot
        self._get_platform_instances = get_platform_instances
        self._iter_platforms_fn = iter_platforms_fn

    async def fetch_items(
        self, configured_ids: list[str] | None = None
    ) -> DirectoryFetchResult:
        groups_by_id: dict[str, dict[str, Any]] = {}
        saw_platform = False
        saw_client = False
        saw_successful_call = False

        runtime = _get_qq_runtime()
        get_platform_bot = self._get_platform_bot or (
            getattr(runtime, "get_platform_bot", None) if runtime else None
        )

        iter_fn = self._iter_platforms_fn or self._iter_qq_platforms
        for platform, platform_id in iter_fn():
            saw_platform = True
            client = get_platform_bot(platform) if get_platform_bot else None
            if client is None or not hasattr(client, "call_action"):
                continue
            saw_client = True
            try:
                result = await client.call_action("get_group_list")
            except Exception as exc:
                logger.warning("[WebAdmin] Failed to load QQ groups: %s", exc)
                continue
            saw_successful_call = True
            for raw_group in self._extract_group_list(result):
                group = self._normalize_group(raw_group, platform_id)
                group_id = group["group_id"]
                if not group_id or group_id in groups_by_id:
                    continue
                groups_by_id[group_id] = group

        if saw_successful_call:
            return DirectoryFetchResult(
                items=list(groups_by_id.values()),
                available=True,
                partial=False,
                message="",
            )

        if saw_client:
            message = "QQ group list request failed."
        elif saw_platform:
            message = "QQ platform found, but no callable client is available."
        else:
            message = "QQ platform is unavailable."

        return DirectoryFetchResult(
            items=[],
            available=False,
            partial=False,
            message=message,
        )

    def get_item_id(self, item: dict[str, Any]) -> str:
        return str(item.get("group_id", "") or "").strip()

    def get_item_aliases(self, item: dict[str, Any]) -> list[str]:
        gid = self.get_item_id(item)
        return [gid] if gid else []

    def normalize_configured_id(self, raw_id: str) -> str:
        normalized = str(raw_id or "").strip()
        return normalized if normalized.isdigit() else ""

    def create_fallback(self, target_id: str) -> dict[str, Any] | None:
        if not target_id or not target_id.isdigit():
            return None
        return self._fallback_group(target_id)

    def sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._sort_groups(items)

    def _iter_qq_platforms(self) -> list[tuple[Any, str]]:
        runtime = _get_qq_runtime()
        get_platform_instances = self._get_platform_instances or (
            getattr(runtime, "get_platform_instances", None) if runtime else None
        )
        if get_platform_instances is None:
            return []
        platforms = get_platform_instances(getattr(self.plugin, "context", None))
        adapter_cls = AiocqhttpAdapter or (
            getattr(runtime, "AiocqhttpAdapter", None) if runtime else None
        )
        return match_qq_platforms(platforms, adapter_cls)

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


class TGDirectoryAdapter(BaseDirectoryAdapter):
    """Directory adapter for Telegram channels using Telethon MTProto client."""

    def __init__(
        self,
        plugin: Any,
        *,
        max_dialogs: int = 2000,
        refresh_timeout: float = 120.0,
    ):
        self.plugin = plugin
        self.max_dialogs = max(1, int(max_dialogs))
        self.refresh_timeout = max(1.0, float(refresh_timeout))

    async def fetch_items(
        self, configured_ids: list[str] | None = None
    ) -> DirectoryFetchResult:
        client = getattr(
            getattr(self.plugin, "client_wrapper", None), "client", None
        )
        if client is None:
            return DirectoryFetchResult(
                items=[],
                available=False,
                partial=False,
                message="Telegram client is unavailable.",
            )

        if not await self._is_client_connected(client):
            return DirectoryFetchResult(
                items=[],
                available=False,
                partial=False,
                message="Telegram client is disconnected.",
            )

        if not await self._is_client_authorized(client):
            return DirectoryFetchResult(
                items=[],
                available=False,
                partial=False,
                message="Telegram client is not authorized.",
            )

        configured = configured_ids or []
        channels_by_ref: dict[str, dict[str, Any]] = {}
        started_at = time.time()

        # 1) 先解析已配置频道：快、且对选择器最有用；不依赖全量对话框。
        resolve_budget = min(30.0, max(5.0, self.refresh_timeout * 0.25))
        resolved = await self._resolve_configured_entities(
            client, configured, timeout=resolve_budget
        )
        channels_by_ref.update(resolved)

        # 2) 再扫对话框补全；总时长不超过 refresh_timeout，避免叠成 150s。
        alias_to_ref = self._build_alias_index(channels_by_ref)
        remaining = max(1.0, self.refresh_timeout - (time.time() - started_at))
        dialog_scan_ok = False
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
                # 同一频道可能以数字 ID 被配置、却以 username 出现在对话框里。
                target_ref = channel_ref
                for alias in self._channel_alias_keys(channel):
                    if alias in alias_to_ref:
                        target_ref = alias_to_ref[alias]
                        break
                existing = channels_by_ref.get(target_ref)
                if existing and existing.get("source") == "live":
                    continue
                if target_ref != channel_ref:
                    channel = {**channel, "channel_ref": target_ref}
                channels_by_ref[target_ref] = channel
                for alias in self._channel_alias_keys(channel):
                    alias_to_ref.setdefault(alias, target_ref)
            dialog_scan_ok = True
        except asyncio.TimeoutError:
            logger.warning(
                "[WebAdmin] Telegram channel dialog scan timed out after %.1fs",
                remaining,
            )
        except Exception as exc:
            logger.warning("[WebAdmin] Failed to load Telegram channels: %s", exc)

        if dialog_scan_ok:
            return DirectoryFetchResult(
                items=list(channels_by_ref.values()),
                available=True,
                partial=False,
                message="",
            )

        if channels_by_ref:
            return DirectoryFetchResult(
                items=list(channels_by_ref.values()),
                available=True,
                partial=True,
                message="完整频道列表加载超时，当前仅显示已配置频道，可稍后手动刷新。",
            )

        return DirectoryFetchResult(
            items=[],
            available=False,
            partial=False,
            message="Telegram 频道列表加载超时，可稍后手动刷新。",
        )

    def get_item_id(self, item: dict[str, Any]) -> str:
        return str(item.get("channel_ref", "") or "").strip()

    def get_item_aliases(self, item: dict[str, Any]) -> list[str]:
        return self._channel_alias_keys(item)

    def normalize_configured_id(self, raw_id: str) -> str:
        return normalize_telegram_channel_name(str(raw_id or ""))

    def create_fallback(self, target_id: str) -> dict[str, Any] | None:
        channel_ref = self.normalize_configured_id(target_id)
        if not channel_ref:
            return None
        return {
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

    def sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._sort_channels(items)

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
                logger.debug(
                    "[WebAdmin] resolve configured channel %s failed: %s", ref, exc
                )
                return ref, None
            dialog = SimpleDialog(entity)
            if not self._is_channel_like(dialog, entity):
                logger.debug(
                    "[WebAdmin] configured Telegram ref is not a channel: %s", ref
                )
                return ref, None
            channel = self._normalize_channel(dialog, entity)
            channel["source"] = "resolved"
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
            logger.warning(
                "[WebAdmin] configured channel resolve timed out after %.1fs", timeout
            )
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
        if hasattr(client, "get_dialogs"):
            try:
                result = client.get_dialogs(limit=self.max_dialogs)
            except TypeError:
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

    @classmethod
    def _channel_alias_keys(cls, channel: dict[str, Any]) -> list[str]:
        keys: list[str] = []

        def push(value: Any) -> None:
            text = str(value or "").strip().lstrip("@")
            if text and text not in keys:
                keys.append(text)

        push(channel.get("channel_ref"))
        push(channel.get("username"))
        entity_id = str(channel.get("id") or "").strip()
        if entity_id:
            bare = entity_id.lstrip("-")
            push(entity_id)
            push(bare)
            push(f"-100{bare}")
            push(cls._private_channel_ref(entity_id))
        return keys

    @classmethod
    def _build_alias_index(
        cls, channels_by_ref: dict[str, dict[str, Any]]
    ) -> dict[str, str]:
        index: dict[str, str] = {}
        for ref, channel in channels_by_ref.items():
            for alias in cls._channel_alias_keys(channel):
                index.setdefault(alias, ref)
        return index

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
