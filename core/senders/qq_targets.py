"""QQ 目标解析与发送异常分类辅助函数。

该模块只保留无状态的纯函数，避免把目标解析与异常归类逻辑散落在发送主流程里。
这样做的目的是让 `QQSender` 只负责编排发送步骤，而把可独立测试的细节下沉到这里。
"""

import json
from collections.abc import Iterable, Mapping

from astrbot.api import logger


def dedupe_keep_order(items: Iterable[str]) -> list[str]:
    """去重并保持元素首次出现顺序。"""
    seen = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def split_qq_targets(targets: list) -> tuple[list[str], list[str]]:
    """把目标拆分为完整会话名和纯数字群号。"""
    session_targets: list[str] = []
    group_ids: list[str] = []
    for raw in targets:
        if raw is None:
            continue
        if isinstance(raw, str):
            val = raw.strip()
        else:
            val = str(raw).strip()
        if not val:
            continue
        if ":" in val:
            session_targets.append(val)
        elif val.isdigit():
            group_ids.append(val)
        else:
            logger.warning(f"[QQSender] Ignore invalid QQ target: {val}")
    return session_targets, group_ids


def session_platform_ids(session_targets: list[str]) -> list[str]:
    """从会话目标字符串中提取去重后的平台 ID。"""
    platform_ids: list[str] = []
    for session in session_targets:
        if ":" not in session:
            continue
        platform_ids.append(session.split(":", 1)[0])
    return dedupe_keep_order(platform_ids)


def _extract_event_ret(message: str) -> Mapping[str, object] | None:
    marker = "EventRet:"
    if marker not in message:
        return None

    payload = message.split(marker, 1)[1]
    json_start = payload.find("{")
    if json_start < 0:
        return None

    candidates = [payload[json_start:]]
    if "\\n" in payload or "\\r" in payload or "\\t" in payload:
        candidates.append(
            payload[json_start:]
            .replace("\\r", "\r")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )

    for candidate in candidates:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, Mapping):
            return parsed
    return None


def _error_result(error: Exception) -> Mapping[str, object] | None:
    result = getattr(error, "result", None)
    if isinstance(result, Mapping):
        return result
    return None


def _error_retcode(error: Exception) -> object:
    result = _error_result(error)
    if result is not None and "retcode" in result:
        return result.get("retcode")
    try:
        return getattr(error, "retcode")
    except Exception:
        return None


def _error_message(error: Exception) -> str:
    result = _error_result(error)
    if result is not None:
        message = result.get("message")
        if isinstance(message, str):
            return message
    return str(error)


def is_sendmsg_confirmation_timeout(error: Exception) -> bool:
    """判断 QQ sendMsg 是否已返回成功但确认事件超时。"""

    rendered = str(error)
    retcode = _error_retcode(error)
    if retcode != 1200 and "retcode=1200" not in rendered:
        return False
    message = _error_message(error)
    if "Timeout:" not in message:
        return False
    if "NodeIKernelMsgService/sendMsg" not in message:
        return False

    event_ret = _extract_event_ret(message)
    if not event_ret:
        return False
    return event_ret.get("result") == 0 and not event_ret.get("errMsg")


def classify_send_error(error: Exception) -> str:
    """把已知发送异常归类为稳定的错误标签。"""
    if is_sendmsg_confirmation_timeout(error):
        return "sendmsg_confirmation_timeout"

    message = _error_message(error)
    rendered = str(error)
    if "WebSocket API call timeout" in message:
        return "timeout"
    if "WebSocket API call timeout" in rendered:
        return "timeout"
    if _error_retcode(error) == 1200 or "retcode=1200" in rendered:
        return "retcode_1200"
    if "wrong session ID" in message or "wrong session ID" in rendered:
        return "wrong_session_id"
    return "send_failed"
