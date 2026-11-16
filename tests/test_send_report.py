from core.send_report import (
    CaptureReason,
    CheckUpdatesReport,
    RuntimeSendReport,
    SendReport,
    SendTerminalReason,
)


def test_runtime_report_identifies_expired_records_without_attempts():
    expired = frozenset({("demo", 3500), ("demo", 3501)})
    report = SendReport(
        reason=SendTerminalReason.NO_ELIGIBLE_RECORDS,
        queue_size_at_start=2,
        remaining_count=0,
        expired=expired,
    )
    capture = CheckUpdatesReport(
        reason=CaptureReason.COMPLETED,
        added_count=2,
    )

    runtime = RuntimeSendReport.combine(capture, report)
    message = runtime.format_message()

    assert runtime.expired_count == 2
    assert runtime.attempted_count == 0
    assert "抓取新增 2 条" in message
    assert "过期清理 2 条，未进入发送" in message
    assert "retention_period" in message
    assert "队列已处理完" not in message


def test_runtime_report_unions_source_identities_across_nested_and_manual_sends():
    monitored = SendReport(
        reason=SendTerminalReason.COMPLETED,
        queue_size_at_start=1,
        remaining_count=0,
        attempted=frozenset({("demo", 1)}),
        acknowledged=frozenset({("demo", 1)}),
    )
    manual = SendReport(
        reason=SendTerminalReason.COMPLETED,
        queue_size_at_start=1,
        remaining_count=1,
        attempted=frozenset({("demo", 1), ("demo", 2)}),
        acknowledged=frozenset({("demo", 1)}),
        failed_retained=frozenset({("demo", 2)}),
    )
    capture = CheckUpdatesReport(
        reason=CaptureReason.COMPLETED,
        added_count=2,
        immediate_send=monitored,
    )

    runtime = RuntimeSendReport.combine(capture, manual)

    assert runtime.attempted_count == 2
    assert runtime.acknowledged_count == 1
    assert runtime.failed_retained_count == 1
    assert runtime.remaining_count == 1


def test_runtime_report_names_empty_queue_instead_of_claiming_success():
    capture = CheckUpdatesReport(
        reason=CaptureReason.COMPLETED,
        added_count=0,
    )
    report = SendReport(
        reason=SendTerminalReason.QUEUE_EMPTY,
        queue_size_at_start=0,
        remaining_count=0,
    )

    message = RuntimeSendReport.combine(capture, report).format_message()

    assert "本次发送未执行（队列为空）" in message
    assert "队列已处理完" not in message


def test_runtime_report_keeps_same_numeric_id_distinct_across_channels():
    report = SendReport(
        reason=SendTerminalReason.COMPLETED,
        queue_size_at_start=2,
        remaining_count=0,
        attempted=frozenset({("channel-a", 7), ("channel-b", 7)}),
        acknowledged=frozenset({("channel-a", 7), ("channel-b", 7)}),
    )
    capture = CheckUpdatesReport(
        reason=CaptureReason.COMPLETED,
        added_count=2,
    )

    runtime = RuntimeSendReport.combine(capture, report)

    assert runtime.attempted_count == 2
    assert runtime.acknowledged_count == 2


def test_runtime_report_keeps_nested_send_completion_when_manual_queue_is_empty():
    monitored = SendReport(
        reason=SendTerminalReason.COMPLETED,
        queue_size_at_start=1,
        remaining_count=0,
        attempted=frozenset({("demo", 42)}),
        acknowledged=frozenset({("demo", 42)}),
    )
    manual = SendReport(
        reason=SendTerminalReason.QUEUE_EMPTY,
        queue_size_at_start=0,
        remaining_count=0,
    )
    capture = CheckUpdatesReport(
        reason=CaptureReason.COMPLETED,
        added_count=1,
        immediate_send=monitored,
    )

    runtime = RuntimeSendReport.combine(capture, manual)
    message = runtime.format_message()

    assert "本次发送完成" in message
    assert "本次发送未执行（队列为空）" not in message
    assert runtime.attempted_count == 1
    assert runtime.acknowledged_count == 1
