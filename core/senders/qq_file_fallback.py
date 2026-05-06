"""QQ 文件发送失败后的兜底策略辅助函数。"""

import os
import posixpath
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Plain

from .qq_batch_builder import ProcessedBatchData
from .qq_media import _patch_file_to_dict
from .qq_types import SendMessageFn

APK_FALLBACK_MODE_ALIASES = {
    "": "off",
    "off": "off",
    "disabled": "off",
    "close": "off",
    "关闭": "off",
    "禁用": "off",
    "zip": "zip",
    "archive": "zip",
    "压缩包": "zip",
    "压缩": "zip",
    "link": "link",
    "direct_link": "link",
    "url": "link",
    "直链": "link",
    "link_or_zip": "link_or_zip",
    "direct_link_or_zip": "link_or_zip",
    "link-first": "link_or_zip",
    "直链优先，失败转压缩包": "link_or_zip",
    "直链优先失败转压缩包": "link_or_zip",
    "直链或压缩包": "link_or_zip",
}
APK_FALLBACK_EXTENSIONS = {".apk", ".xapk", ".apkm", ".apks"}


@dataclass(frozen=True)
class ApkFallbackPolicy:
    mode: str
    direct_link_base_url: str


def normalize_apk_fallback_mode(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"off", "zip", "link", "link_or_zip"}:
        return raw
    return APK_FALLBACK_MODE_ALIASES.get(raw, "off")


def resolve_apk_fallback_policy(forward_cfg: Mapping[str, object]) -> ApkFallbackPolicy:
    return ApkFallbackPolicy(
        mode=normalize_apk_fallback_mode(
            forward_cfg.get("apk_fallback_mode", "link_or_zip")
        ),
        direct_link_base_url=str(
            forward_cfg.get("apk_direct_link_base_url", "") or ""
        ).strip(),
    )


def _get_file_component_name(component: File) -> str:
    for attr_name in ("name",):
        value = getattr(component, attr_name, None)
        if value:
            return os.path.basename(str(value))
    for attr_name in ("_tgf_source_path", "file", "file_"):
        value = getattr(component, attr_name, None)
        if value:
            return os.path.basename(str(value))
    return "download.apk"


def _get_file_component_source_path(component: File) -> str:
    source_path = getattr(component, "_tgf_source_path", None)
    if source_path:
        return str(source_path)
    for attr_name in ("file", "file_"):
        value = getattr(component, attr_name, None)
        if isinstance(value, str) and value and "://" not in value:
            return value
    return ""


def _is_apk_component(component: File) -> bool:
    name = _get_file_component_name(component).lower()
    _, ext = os.path.splitext(name)
    if ext in APK_FALLBACK_EXTENSIONS:
        return True
    source_path = _get_file_component_source_path(component).lower()
    _, source_ext = os.path.splitext(source_path)
    return source_ext in APK_FALLBACK_EXTENSIONS


def _is_apk_rich_media_failure(
    error: Exception, classify_send_error: Callable[[Exception], str]
) -> bool:
    message = str(error).lower()
    return (
        "rich media transfer failed" in message
        or "retcode=1200" in message
        or classify_send_error(error) == "retcode_1200"
    )


def _build_direct_link(base_url: str, filename: str) -> str:
    parts = urlsplit(base_url)
    base_path = parts.path or "/"
    if not base_path.endswith("/"):
        base_path += "/"
    full_path = posixpath.join(base_path, quote(filename))
    return urlunsplit(
        (parts.scheme, parts.netloc, full_path, parts.query, parts.fragment)
    )


def _build_file_component(
    local_path: str,
    display_name: str,
    *,
    map_path: Callable[[str], str],
) -> File:
    mapped = map_path(local_path)
    component = _patch_file_to_dict(
        File(
            file=mapped,
            url="",
            name=display_name,
        )
    )
    try:
        object.__setattr__(component, "_tgf_source_path", local_path)
    except Exception:
        component.__dict__["_tgf_source_path"] = local_path
    return component


def _create_apk_zip_archive(
    source_path: str, original_name: str, *, plugin_data_dir: str | None
) -> tuple[str, str]:
    target_dir = plugin_data_dir or os.path.dirname(source_path) or "."
    archive_name = f"{original_name}.zip"
    archive_path = os.path.join(target_dir, archive_name)
    suffix = 1
    while os.path.exists(archive_path):
        archive_name = f"{original_name}.{suffix}.zip"
        archive_path = os.path.join(target_dir, archive_name)
        suffix += 1
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.write(source_path, arcname=original_name)
    return archive_path, archive_name


async def handle_apk_file_send_failure(
    *,
    policy: ApkFallbackPolicy,
    component: File,
    error: Exception,
    batch_data: ProcessedBatchData,
    unified_msg_origin: str,
    target_session: str,
    send_message_fn: SendMessageFn,
    map_path: Callable[[str], str],
    classify_send_error: Callable[[Exception], str],
    plugin_data_dir: str | None,
) -> bool:
    if policy.mode == "off" or not _is_apk_component(component):
        return False
    if not _is_apk_rich_media_failure(error, classify_send_error):
        return False

    original_name = _get_file_component_name(component)

    if policy.mode in {"link", "link_or_zip"} and policy.direct_link_base_url:
        direct_link = _build_direct_link(policy.direct_link_base_url, original_name)
        try:
            await send_message_fn(
                unified_msg_origin,
                MessageChain(
                    [
                        Plain(
                            f"原文件 {original_name} 因 QQ 文件限制无法直传，改发直链：{direct_link}"
                        )
                    ]
                ),
                send_kind="fallback_link",
            )
        except Exception as link_error:
            if policy.mode == "link":
                raise
            logger.warning(
                f"[QQSender] APK 文件直链降级发送失败，继续尝试压缩包兜底: target={target_session}, file={original_name}, error={link_error}"
            )
        else:
            logger.warning(
                f"[QQSender] APK 文件发送失败，已降级为直链: target={target_session}, file={original_name}"
            )
            return True

    if policy.mode not in {"zip", "link_or_zip"}:
        return False

    source_path = _get_file_component_source_path(component)
    if not source_path or not os.path.isfile(source_path):
        logger.warning(
            f"[QQSender] APK 文件发送失败且缺少可访问源文件，无法压缩兜底: target={target_session}, file={original_name}, source={source_path!r}"
        )
        return False

    archive_path, archive_name = _create_apk_zip_archive(
        source_path, original_name, plugin_data_dir=plugin_data_dir
    )
    batch_data.setdefault("local_files", []).append(archive_path)
    archive_component = _build_file_component(
        archive_path, archive_name, map_path=map_path
    )
    await send_message_fn(
        unified_msg_origin,
        MessageChain([archive_component]),
        send_kind="fallback_zip",
    )
    logger.warning(
        f"[QQSender] APK 文件发送失败，已降级为压缩包: target={target_session}, file={original_name}, archive={archive_name}"
    )
    return True
