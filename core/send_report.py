from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

PendingMessageKey = tuple[str, int]


class SendTerminalReason(str, Enum):
    COMPLETED = "completed"
    QUEUE_EMPTY = "queue_empty"
    NO_ELIGIBLE_RECORDS = "no_eligible_records"
    NO_MONITORED_MATCH = "no_monitored_match"
    WAITING_FOR_RETRY = "waiting_for_retry"
    CLIENT_UNAVAILABLE = "client_unavailable"
    NO_DESTINATION = "no_destination"
    RETAINED_FAILURES = "retained_failures"
    NO_SENDABLE_BATCHES = "no_sendable_batches"
    STOP_REQUESTED = "stop_requested"
    QUEUE_CLEARED = "queue_cleared"
    CURFEW = "curfew"
    CANCELLED = "cancelled"


class CaptureReason(str, Enum):
    COMPLETED = "completed"
    QUEUE_EMPTY = "queue_empty"
    STOP_REQUESTED = "stop_requested"
    QUEUE_CLEARED = "queue_cleared"
    CLIENT_UNAVAILABLE = "client_unavailable"
    CURFEW = "curfew"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SendReport:
    """一次队列处理操作的可核对结果。

    `attempted` 只包含至少调用了一个发送目标的消息；`acknowledged` 只包含
    所有已配置目标都确认成功的消息。没有目标、未确认移除和仍然保留的记录
    因此不会再被队列数量变化误当成发送成功。
    """

    reason: SendTerminalReason
    queue_size_at_start: int = 0
    remaining_count: int = 0

    expired: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    filtered: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    refetch_miss_retained: frozenset[PendingMessageKey] = field(
        default_factory=frozenset
    )
    refetch_miss_removed: frozenset[PendingMessageKey] = field(
        default_factory=frozenset
    )
    outside_monitor_scope: frozenset[PendingMessageKey] = field(
        default_factory=frozenset
    )
    waiting_for_retry: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    not_selected: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    no_destination: frozenset[PendingMessageKey] = field(default_factory=frozenset)

    attempted: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    acknowledged: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    failed_retained: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    deferred_retained: frozenset[PendingMessageKey] = field(default_factory=frozenset)
    removed_without_ack: frozenset[PendingMessageKey] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in (
            "expired",
            "filtered",
            "refetch_miss_retained",
            "refetch_miss_removed",
            "outside_monitor_scope",
            "waiting_for_retry",
            "not_selected",
            "no_destination",
            "attempted",
            "acknowledged",
            "failed_retained",
            "deferred_retained",
            "removed_without_ack",
        ):
            object.__setattr__(self, name, frozenset(getattr(self, name)))

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "queue_size_at_start": self.queue_size_at_start,
            "remaining_count": self.remaining_count,
            "expired_count": len(self.expired),
            "filtered_count": len(self.filtered),
            "refetch_miss_retained_count": len(self.refetch_miss_retained),
            "refetch_miss_removed_count": len(self.refetch_miss_removed),
            "outside_monitor_scope_count": len(self.outside_monitor_scope),
            "waiting_for_retry_count": len(self.waiting_for_retry),
            "not_selected_count": len(self.not_selected),
            "no_destination_count": len(self.no_destination),
            "attempted_count": len(self.attempted),
            "acknowledged_count": len(self.acknowledged),
            "failed_retained_count": len(self.failed_retained),
            "deferred_retained_count": len(self.deferred_retained),
            "removed_without_ack_count": len(self.removed_without_ack),
        }


@dataclass(frozen=True)
class CheckUpdatesReport:
    added_count: int = 0
    reason: CaptureReason = CaptureReason.COMPLETED
    immediate_send: SendReport | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason.value,
            "added_count": self.added_count,
            "immediate_send": (
                self.immediate_send.to_dict()
                if self.immediate_send is not None
                else None
            ),
        }


@dataclass
class SendReportBuilder:
    queue_size_at_start: int

    expired: set[PendingMessageKey] = field(default_factory=set)
    filtered: set[PendingMessageKey] = field(default_factory=set)
    refetch_miss_retained: set[PendingMessageKey] = field(default_factory=set)
    refetch_miss_removed: set[PendingMessageKey] = field(default_factory=set)
    outside_monitor_scope: set[PendingMessageKey] = field(default_factory=set)
    waiting_for_retry: set[PendingMessageKey] = field(default_factory=set)
    not_selected: set[PendingMessageKey] = field(default_factory=set)
    no_destination: set[PendingMessageKey] = field(default_factory=set)
    attempted: set[PendingMessageKey] = field(default_factory=set)
    acknowledged: set[PendingMessageKey] = field(default_factory=set)
    failed_retained: set[PendingMessageKey] = field(default_factory=set)
    deferred_retained: set[PendingMessageKey] = field(default_factory=set)
    removed_without_ack: set[PendingMessageKey] = field(default_factory=set)

    def build(
        self,
        reason: SendTerminalReason,
        remaining_count: int,
    ) -> SendReport:
        return SendReport(
            reason=reason,
            queue_size_at_start=self.queue_size_at_start,
            remaining_count=remaining_count,
            expired=frozenset(self.expired),
            filtered=frozenset(self.filtered),
            refetch_miss_retained=frozenset(self.refetch_miss_retained),
            refetch_miss_removed=frozenset(self.refetch_miss_removed),
            outside_monitor_scope=frozenset(self.outside_monitor_scope),
            waiting_for_retry=frozenset(self.waiting_for_retry),
            not_selected=frozenset(self.not_selected),
            no_destination=frozenset(self.no_destination),
            attempted=frozenset(self.attempted),
            acknowledged=frozenset(self.acknowledged),
            failed_retained=frozenset(self.failed_retained),
            deferred_retained=frozenset(self.deferred_retained),
            removed_without_ack=frozenset(self.removed_without_ack),
        )


@dataclass(frozen=True)
class RuntimeSendReport:
    capture: CheckUpdatesReport
    send_reports: tuple[SendReport, ...]

    @classmethod
    def combine(
        cls,
        capture: CheckUpdatesReport,
        manual_send: SendReport | None,
    ) -> "RuntimeSendReport":
        reports: list[SendReport] = []
        if capture.immediate_send is not None:
            reports.append(capture.immediate_send)
        if manual_send is not None:
            reports.append(manual_send)
        return cls(capture=capture, send_reports=tuple(reports))

    def _union(self, field_name: str) -> frozenset[PendingMessageKey]:
        values: Iterable[Iterable[PendingMessageKey]] = (
            getattr(report, field_name) for report in self.send_reports
        )
        return frozenset().union(*values)

    @property
    def added_count(self) -> int:
        return self.capture.added_count

    @property
    def expired_count(self) -> int:
        return len(self._union("expired"))

    @property
    def filtered_count(self) -> int:
        return len(self._union("filtered"))

    @property
    def no_destination_count(self) -> int:
        return len(self._union("no_destination"))

    @property
    def attempted_count(self) -> int:
        return len(self._union("attempted"))

    @property
    def acknowledged_count(self) -> int:
        return len(self._union("acknowledged"))

    @property
    def failed_retained_count(self) -> int:
        return len(self._union("failed_retained"))

    @property
    def deferred_retained_count(self) -> int:
        return len(self._union("deferred_retained"))

    @property
    def removed_without_ack_count(self) -> int:
        return len(self._union("removed_without_ack"))

    @property
    def refetch_miss_retained_count(self) -> int:
        return len(self._union("refetch_miss_retained"))

    @property
    def refetch_miss_removed_count(self) -> int:
        return len(self._union("refetch_miss_removed"))

    @property
    def remaining_count(self) -> int:
        if self.send_reports:
            return self.send_reports[-1].remaining_count
        return 0

    @property
    def send_reason(self) -> SendTerminalReason:
        attention_reasons = {
            SendTerminalReason.CANCELLED.value,
            SendTerminalReason.STOP_REQUESTED.value,
            SendTerminalReason.QUEUE_CLEARED.value,
            SendTerminalReason.CLIENT_UNAVAILABLE.value,
            SendTerminalReason.RETAINED_FAILURES.value,
            SendTerminalReason.NO_DESTINATION.value,
        }
        for report in self.send_reports:
            if _enum_value(report.reason) in attention_reasons:
                return report.reason
        if self.send_reports:
            return self.send_reports[-1].reason
        return SendTerminalReason.COMPLETED

    def format_message(self) -> str:
        capture_prefix = (
            f"抓取新增 {self.added_count} 条"
            if _enum_value(self.capture.reason) == CaptureReason.COMPLETED.value
            else (
                f"抓取未完成（{_capture_reason_label(self.capture.reason)}），"
                f"新增 {self.added_count} 条"
            )
        )
        has_send_activity = any(
            report.attempted
            or report.acknowledged
            or report.failed_retained
            or report.deferred_retained
            or report.removed_without_ack
            for report in self.send_reports
        )
        send_reason_value = _enum_value(self.send_reason)
        partial_reasons = {
            SendTerminalReason.STOP_REQUESTED.value,
            SendTerminalReason.QUEUE_CLEARED.value,
            SendTerminalReason.CLIENT_UNAVAILABLE.value,
            SendTerminalReason.RETAINED_FAILURES.value,
            SendTerminalReason.CANCELLED.value,
        }
        if has_send_activity and send_reason_value in partial_reasons:
            send_prefix = (
                "本次发送部分完成"
                f"（{_send_reason_label(self.send_reason)}）"
            )
        elif has_send_activity and self.removed_without_ack_count:
            send_prefix = (
                "本次发送部分确认"
                if self.acknowledged_count
                else "本次发送结果未确认"
            )
        elif (
            has_send_activity
            or send_reason_value == SendTerminalReason.COMPLETED.value
        ):
            send_prefix = "本次发送完成"
        else:
            send_prefix = f"本次发送未执行（{_send_reason_label(self.send_reason)}）"

        summary = (
            f"{capture_prefix}；{send_prefix}："
            f"尝试发送 {self.attempted_count} 条，"
            f"确认移除 {self.acknowledged_count} 条，"
            f"过期清理 {self.expired_count} 条"
            f"{'，未进入发送' if self.expired_count else ''}，"
            f"过滤移除 {self.filtered_count} 条，"
            f"失败保留 {self.failed_retained_count} 条，"
            f"延后保留 {self.deferred_retained_count} 条，"
            f"未确认移除 {self.removed_without_ack_count} 条，"
            f"无发送目标 {self.no_destination_count} 条，"
            f"剩余 {self.remaining_count} 条。"
        )
        details = []
        if self.expired_count:
            details.append(
                f"{self.expired_count} 条消息超过 retention_period，未进入发送。"
            )
        if self.refetch_miss_retained_count:
            details.append(
                f"另有 {self.refetch_miss_retained_count} 条重取失败，已保留等待重试。"
            )
        if self.refetch_miss_removed_count:
            details.append(
                f"另有 {self.refetch_miss_removed_count} 条重取失败次数超限，已移除。"
            )
        if self.no_destination_count:
            details.append(
                f"{self.no_destination_count} 条消息没有配置 QQ 或 Telegram 目标，"
                "未进入发送。"
            )
        return " ".join([summary, *details])

    def to_dict(self) -> dict[str, object]:
        return {
            "capture_reason": self.capture.reason.value,
            "added_count": self.added_count,
            "send_reasons": [report.reason.value for report in self.send_reports],
            "expired_count": self.expired_count,
            "filtered_count": self.filtered_count,
            "no_destination_count": self.no_destination_count,
            "refetch_miss_retained_count": self.refetch_miss_retained_count,
            "refetch_miss_removed_count": self.refetch_miss_removed_count,
            "attempted_count": self.attempted_count,
            "acknowledged_count": self.acknowledged_count,
            "failed_retained_count": self.failed_retained_count,
            "deferred_retained_count": self.deferred_retained_count,
            "removed_without_ack_count": self.removed_without_ack_count,
            "remaining_count": self.remaining_count,
        }


def _enum_value(reason: SendTerminalReason | CaptureReason) -> str:
    return str(getattr(reason, "value", reason))


def _send_reason_label(reason: SendTerminalReason) -> str:
    labels = {
        SendTerminalReason.COMPLETED.value: "已完成",
        SendTerminalReason.QUEUE_EMPTY.value: "队列为空",
        SendTerminalReason.NO_ELIGIBLE_RECORDS.value: "没有符合条件的记录",
        SendTerminalReason.NO_MONITORED_MATCH.value: "监听范围没有匹配记录",
        SendTerminalReason.WAITING_FOR_RETRY.value: "记录均处于重试等待期",
        SendTerminalReason.CLIENT_UNAVAILABLE.value: "Telegram 客户端不可用",
        SendTerminalReason.NO_DESTINATION.value: "没有配置发送目标",
        SendTerminalReason.RETAINED_FAILURES.value: "存在失败保留",
        SendTerminalReason.NO_SENDABLE_BATCHES.value: "没有形成可发送批次",
        SendTerminalReason.STOP_REQUESTED.value: "已请求停止",
        SendTerminalReason.QUEUE_CLEARED.value: "队列已清空",
        SendTerminalReason.CURFEW.value: "当前处于宵禁时间",
        SendTerminalReason.CANCELLED.value: "任务已取消",
    }
    return labels.get(_enum_value(reason), str(reason))


def _capture_reason_label(reason: CaptureReason) -> str:
    labels = {
        CaptureReason.COMPLETED.value: "已完成",
        CaptureReason.QUEUE_EMPTY.value: "没有新增消息",
        CaptureReason.STOP_REQUESTED.value: "已请求停止",
        CaptureReason.QUEUE_CLEARED.value: "队列已清空",
        CaptureReason.CLIENT_UNAVAILABLE.value: "Telegram 客户端不可用",
        CaptureReason.CURFEW.value: "当前处于宵禁时间",
        CaptureReason.CANCELLED.value: "任务已取消",
    }
    return labels.get(_enum_value(reason), str(reason))
