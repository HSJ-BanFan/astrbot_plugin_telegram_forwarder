"""OneBot 11 adapter used by the QQ sender facade."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path

from ..recall import (
    RecallContext,
    RecallRegistry,
    SendReceipt,
    TerminalRecallError,
    TransientRecallError,
    classify_recall_error,
    extract_message_ids,
    is_transient_recall_error,
    schedule_recall_receipts,
)
from .qq_types import SendKind

FallbackSendFn = Callable[[list[object]], Awaitable[object]]


async def component_to_onebot_dict(component: object) -> dict:
    """Convert one AstrBot component to a OneBot message segment."""
    component_name = type(component).__name__
    if component_name in {"Image", "Record"}:
        convert_to_base64 = getattr(component, "convert_to_base64", None)
        if convert_to_base64 is not None:
            encoded = convert_to_base64()
            if inspect.isawaitable(encoded):
                encoded = await encoded
            return {
                "type": component_name.lower(),
                "data": {"file": f"base64://{encoded}"},
            }

    converter = getattr(component, "to_dict", None)
    if converter is None:
        converter = getattr(component, "toDict", None)
    payload = converter() if converter is not None else None
    if inspect.isawaitable(payload):
        payload = await payload
    if isinstance(payload, Mapping):
        payload = dict(payload)
        if component_name == "File":
            data = payload.get("data")
            file_value = data.get("file") if isinstance(data, Mapping) else None
            if isinstance(file_value, str) and file_value:
                try:
                    path_obj = Path(file_value)
                    if path_obj.is_absolute() and "://" not in file_value:
                        payload["data"] = {**data, "file": path_obj.as_uri()}
                except (OSError, ValueError):
                    return payload
        if "data" in payload and "type" in payload:
            return payload
        if component_name == "Plain" and "text" in payload:
            return {"type": "text", "data": {"text": payload["text"]}}
        return payload
    if component_name == "Plain":
        return {
            "type": "text",
            "data": {"text": str(getattr(component, "text", ""))},
        }
    return {"type": component_name.lower(), "data": {}}


def onebot_target(unified_msg_origin: str) -> tuple[str, str, int] | None:
    """Resolve an AstrBot session origin to its OneBot action pair."""
    parts = str(unified_msg_origin).split(":", 2)
    if len(parts) != 3:
        return None
    message_type = parts[1].lower()
    try:
        target_id = int(parts[2])
    except (TypeError, ValueError):
        return None
    if "group" in message_type:
        return "send_group_msg", "send_group_forward_msg", target_id
    if "friend" in message_type or "private" in message_type:
        return "send_private_msg", "send_private_forward_msg", target_id
    return None


def action_failed(result: object) -> bool:
    """Return whether a OneBot response explicitly reports failure."""
    if not isinstance(result, Mapping):
        return False
    status = result.get("status")
    retcode = result.get("retcode")
    if status == "failed":
        return True
    if retcode is None:
        return False
    try:
        return int(retcode) != 0
    except (TypeError, ValueError):
        return True


def _is_transient_onebot_response(result: object) -> bool:
    if not isinstance(result, Mapping):
        return False
    try:
        retcode = int(result.get("retcode", 0))
    except (TypeError, ValueError):
        retcode = 0
    if retcode == 1200:
        return True
    return is_transient_recall_error(RuntimeError(repr(result)))


async def call_onebot_action(
    call_action: Callable[..., object], action: str, **kwargs: object
) -> object:
    """Invoke one OneBot action and await its result when necessary."""
    result = call_action(action, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def ensure_action_succeeded(
    result: object, action: str, *, for_recall: bool = False
) -> None:
    """Raise the right error class when OneBot explicitly rejects an action."""
    if not action_failed(result):
        return
    error = RuntimeError(f"{action} failed: {result!r}")
    if not for_recall:
        raise error
    if _is_transient_onebot_response(result):
        raise TransientRecallError(str(error))
    classified = classify_recall_error(error)
    if classified is not error:
        raise classified from error
    raise TerminalRecallError(str(error))


class QQOneBotAdapter:
    """Hide OneBot payload, action, and receipt details from ``QQSender``."""

    def __init__(
        self,
        bot: object | None,
        config: object,
        recall_registry: RecallRegistry | None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.recall_registry = recall_registry

    async def recall(self, receipt: SendReceipt) -> None:
        """Recall one QQ message through the bot that produced its receipt."""
        try:
            message_id = int(receipt.message_id)
        except (TypeError, ValueError) as exc:
            raise TerminalRecallError("invalid QQ message ID") from exc
        call_action = getattr(self.bot, "call_action", None)
        if not callable(call_action):
            raise TerminalRecallError("QQ bot no longer exposes call_action")
        try:
            result = await call_onebot_action(
                call_action,
                "delete_msg",
                message_id=message_id,
            )
        except Exception as exc:
            classified = classify_recall_error(exc)
            if classified is not exc:
                raise classified from exc
            raise
        ensure_action_succeeded(result, "delete_msg", for_recall=True)

    def schedule_recall(
        self,
        message_ids: Iterable[object],
        *,
        target_session: str,
        source_channel: str,
        kind: SendKind,
    ) -> None:
        if self.bot is None:
            return
        schedule_recall_receipts(
            self.recall_registry,
            self.config,
            message_ids,
            context=RecallContext(
                platform="qq-onebot",
                target_session=target_session,
                source_channel=source_channel,
                kind=kind,
            ),
            enabled_key="qq_enabled",
            delay_key="qq_delay_seconds",
            recall_fn=self.recall,
        )

    async def send(
        self,
        unified_msg_origin: str,
        message_chain: object,
        *,
        target_session: str,
        source_channel: str,
        kind: SendKind,
        fallback_send: FallbackSendFn | None = None,
    ) -> list[str] | None:
        """Send through OneBot, or return ``None`` when the adapter is unavailable."""
        call_action = (
            getattr(self.bot, "call_action", None) if self.bot is not None else None
        )
        if not callable(call_action):
            return None
        target = onebot_target(unified_msg_origin)
        if target is None:
            return None
        send_action, forward_action, target_id = target
        target_key = "group_id" if send_action == "send_group_msg" else "user_id"
        route = {target_key: target_id}
        message_ids: list[str] = []

        fallback_attempted = False

        def record_ids(new_ids: list[str]) -> None:
            message_ids.extend(new_ids)
            self.schedule_recall(
                new_ids,
                target_session=target_session,
                source_channel=source_channel,
                kind=kind,
            )

        async def send_action_payload(
            action: str, fallback_components: list[object], **payload: object
        ) -> None:
            nonlocal fallback_attempted
            result = await call_onebot_action(call_action, action, **route, **payload)
            ensure_action_succeeded(result, action)
            new_ids = extract_message_ids(result)
            if new_ids:
                record_ids(new_ids)
                return
            if fallback_send is None:
                return
            fallback_attempted = True
            fallback_result = await fallback_send(fallback_components)
            record_ids(extract_message_ids(fallback_result))

        async def send_components(components: list[object]) -> None:
            if not components:
                return
            messages = []
            fallback_components = []
            for component in components:
                if (
                    type(component).__name__ == "Plain"
                    and not str(getattr(component, "text", "")).strip()
                ):
                    continue
                fallback_components.append(component)
                payload = await component_to_onebot_dict(component)
                messages.append(payload)
                if type(component).__name__ in {"At", "AtAll"}:
                    messages.append({"type": "text", "data": {"text": " "}})
            if not messages:
                return
            await send_action_payload(
                send_action, fallback_components, message=messages
            )

        async def send_forward(component: object) -> None:
            component_name = type(component).__name__
            payload = await component_to_onebot_dict(component)
            if component_name == "Node":
                payload = {"messages": [payload]}
            await send_action_payload(forward_action, [component], **payload)

        components = list(getattr(message_chain, "chain", []))
        if not any(
            type(component).__name__ in {"Node", "Nodes", "File"}
            for component in components
        ):
            await send_components(components)
            return message_ids if fallback_attempted else message_ids or None

        for component in components:
            if type(component).__name__ in {"Node", "Nodes"}:
                await send_forward(component)
            else:
                await send_components([component])
        return message_ids if fallback_attempted else message_ids or None
