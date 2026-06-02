import hashlib
import re
from datetime import datetime, timezone

from telethon.tl.types import Message

from astrbot.api import logger

from .base import MergeRule


class KeywordNextNMerge(MergeRule):
    """触发关键词消息 + 后续 N 条消息合并为一个逻辑组。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.trigger_keywords = self._normalize_keywords(
            config.get("trigger_keywords")
            or config.get("keywords")
            or config.get("keyword")
            or []
        )
        self.trigger_regex = str(config.get("trigger_regex", "") or "").strip()
        self.next_count = self._positive_int(
            config.get("next_count", config.get("merge_count", config.get("count", 2))),
            2,
        )
        self.time_window_seconds = self._positive_int(
            config.get("time_window_seconds", 60),
            60,
        )

    def can_merge(
        self, channel_name: str, msg1: tuple[str, Message], msg2: tuple[str, Message]
    ) -> bool:
        _, message1 = msg1
        _, message2 = msg2
        return self._is_trigger_message(message1) and self._within_window(
            message1, message2
        )

    def find_group(
        self,
        start_index: int,
        messages: list[tuple[str, Message]],
        channel_name: str,
        used_indices: set,
    ) -> dict:
        start_msg = messages[start_index]
        _, trigger = start_msg
        if not self._is_trigger_message(trigger):
            return {"messages": [start_msg], "indices": [start_index]}

        group_messages = [start_msg]
        group_indices = [start_index]
        window_closed_by_later_message = False

        for i in range(start_index + 1, len(messages)):
            if i in used_indices:
                continue

            candidate = messages[i]
            candidate_channel, candidate_msg = candidate
            if candidate_channel != channel_name:
                continue

            time_diff = self._time_diff_seconds(trigger, candidate_msg)
            if time_diff is None:
                break
            if time_diff < 0:
                continue
            if time_diff > self.time_window_seconds:
                window_closed_by_later_message = True
                break

            group_messages.append(candidate)
            group_indices.append(i)

            if len(group_messages) - 1 >= self.next_count:
                break

        has_enough_next = len(group_messages) - 1 >= self.next_count
        window_expired = self._age_seconds(trigger) > self.time_window_seconds
        if has_enough_next or (
            len(group_messages) > 1
            and (window_expired or window_closed_by_later_message)
        ):
            return {"messages": group_messages, "indices": group_indices}

        return {"messages": [start_msg], "indices": [start_index]}

    def find_defer_from_index(
        self,
        messages: list[tuple[str, Message]],
        channel_name: str,
        now: datetime | float | int | None = None,
    ) -> int | None:
        if not self._has_trigger_condition():
            return None

        for start_index, (message_channel, trigger) in enumerate(messages):
            if message_channel != channel_name:
                continue
            if not self._is_trigger_message(trigger):
                continue

            next_seen = 0
            window_closed_by_later_message = False
            for _, candidate in messages[start_index + 1 :]:
                time_diff = self._time_diff_seconds(trigger, candidate)
                if time_diff is None:
                    window_closed_by_later_message = True
                    break
                if time_diff < 0:
                    continue
                if time_diff > self.time_window_seconds:
                    window_closed_by_later_message = True
                    break

                next_seen += 1
                if next_seen >= self.next_count:
                    break

            if next_seen >= self.next_count:
                continue
            if window_closed_by_later_message:
                continue
            if self._age_seconds(trigger, now=now) > self.time_window_seconds:
                continue
            return start_index

        return None

    def get_group_key(self, msg: tuple[str, Message]) -> str | None:
        channel_name, message = msg
        if not self._is_trigger_message(message):
            return None
        return f"{channel_name}:keyword-next-n:{message.id}"

    def apply_merge_marker(
        self, messages: list[tuple[str, Message]], group_key: str
    ) -> None:
        group_id = self._stable_group_id(group_key)
        for _, msg in messages:
            setattr(msg, "_merge_group_id", group_id)
            setattr(msg, "_merge_rule_class", self.__class__.__name__)

        logger.info(
            f"[KeywordNextNMerge] Merged {len(messages)} messages with key={group_key}, grouped_id={group_id}"
        )

    def _has_trigger_condition(self) -> bool:
        return bool(self.trigger_keywords or self.trigger_regex)

    def _is_trigger_message(self, msg: Message) -> bool:
        if not self._has_trigger_condition():
            return False

        full_text = self._build_message_search_text(msg)
        if not full_text:
            return False

        check_text_lower = full_text.lower()
        for keyword in self.trigger_keywords:
            if self._is_keyword_matched(keyword, check_text_lower):
                return True

        if self.trigger_regex:
            try:
                return bool(
                    re.search(self.trigger_regex, full_text, re.IGNORECASE | re.DOTALL)
                )
            except re.error as e:
                logger.error(
                    f"[KeywordNextNMerge] 非法触发正则 '{self.trigger_regex}': {e}"
                )
        return False

    @staticmethod
    def _normalize_keywords(value) -> list[str]:
        if isinstance(value, str):
            raw_items = re.split(r"[\n,]", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        return [str(item).strip() for item in raw_items if str(item).strip()]

    @staticmethod
    def _positive_int(value, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(1, parsed)

    @staticmethod
    def _build_message_search_text(msg: Message) -> str:
        text_content = getattr(msg, "text", None) or ""
        button_text = ""
        reply_markup = getattr(msg, "reply_markup", None)
        if reply_markup and hasattr(reply_markup, "rows"):
            button_parts = []
            for row in reply_markup.rows:
                for btn in row.buttons:
                    if hasattr(btn, "text") and btn.text:
                        button_parts.append(btn.text)
            button_text = " ".join(button_parts)
        return f"{text_content} {button_text}".strip()

    @staticmethod
    def _is_keyword_matched(pattern_str: str, text: str) -> bool:
        if not pattern_str or not text:
            return False
        pattern_str = pattern_str.lower().strip()
        if not pattern_str:
            return False
        if pattern_str.isascii():
            regex_pattern = rf"(?<![a-zA-Z0-9]){re.escape(pattern_str)}(?![a-zA-Z0-9])"
            return bool(re.search(regex_pattern, text, re.IGNORECASE))
        return pattern_str in text

    def _within_window(self, first: Message, second: Message) -> bool:
        time_diff = self._time_diff_seconds(first, second)
        return time_diff is not None and 0 <= time_diff <= self.time_window_seconds

    @staticmethod
    def _message_timestamp(msg: Message) -> float | None:
        msg_date = getattr(msg, "date", None)
        if isinstance(msg_date, datetime):
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            return msg_date.timestamp()
        if isinstance(msg_date, (int, float)):
            return float(msg_date)
        return None

    def _time_diff_seconds(self, first: Message, second: Message) -> float | None:
        first_ts = self._message_timestamp(first)
        second_ts = self._message_timestamp(second)
        if first_ts is None or second_ts is None:
            return None
        return second_ts - first_ts

    def _age_seconds(
        self, msg: Message, now: datetime | float | int | None = None
    ) -> float:
        msg_ts = self._message_timestamp(msg)
        if msg_ts is None:
            return float("inf")

        if isinstance(now, datetime):
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            now_ts = now.timestamp()
        elif isinstance(now, (int, float)):
            now_ts = float(now)
        else:
            now_ts = datetime.now(timezone.utc).timestamp()
        return now_ts - msg_ts

    @staticmethod
    def _stable_group_id(group_key: str) -> int:
        digest = hashlib.sha1(group_key.encode("utf-8")).hexdigest()
        return int(digest[:15], 16)
