"""QQLogPolicy debug 开关测试。"""

import conftest as plugin_conftest


class TestQQLogPolicy:
    def setup_method(self) -> None:
        plugin_conftest.mock_logger.reset_mock()

    def test_log_send_success_skips_when_debug_disabled(self, sender) -> None:
        sender._log_policy.log_send_success(
            send_kind="video",
            target="group-1",
            component_types=["Video"],
            payload_file="file:///tmp/video.mp4",
            source_path="/tmp/video.mp4",
            duration=0.0,
        )

        plugin_conftest.mock_logger.info.assert_not_called()

    def test_log_video_dispatch_prepared_skips_when_debug_disabled(self, sender) -> None:
        sender._log_policy.log_video_dispatch_prepared(
            source_path="/tmp/source.mp4",
            mapped_path="/mapped/source.mp4",
            file="file:///mapped/source.mp4",
            ext=".mp4",
            file_size=1024,
            mapped_changed=True,
        )

        plugin_conftest.mock_logger.info.assert_not_called()

    def test_log_special_media_ready_skips_when_debug_disabled(self, sender) -> None:
        sender._log_policy.log_special_media_ready(
            target="group-1",
            batch_index=2,
            node_types=["Plain", "Video"],
            source_path=None,
            payload_file="file:///mapped/source.mp4",
            file_size=2048,
            has_plain_text_same_batch=False,
        )

        plugin_conftest.mock_logger.info.assert_not_called()

    def test_log_merge_send_skips_when_debug_disabled(self, sender) -> None:
        sender._log_policy.log_merge_send(
            node_name="bot",
            target="group-1",
            label="大合并转发",
            chunk_idx=1,
            total_chunks=3,
            node_count=8,
            batch_count=2,
        )

        plugin_conftest.mock_logger.info.assert_not_called()

    def test_log_audio_split_skips_when_debug_disabled(self, sender) -> None:
        sender._log_policy.log_audio_split(node_name="bot", target="group-1")

        plugin_conftest.mock_logger.info.assert_not_called()

    def test_log_send_success_logs_when_debug_enabled(self, sender) -> None:
        sender.config["debug_enabled_default"] = True

        sender._log_policy.log_send_success(
            send_kind="video",
            target="group-1",
            component_types=["Video"],
            payload_file="file:///tmp/video.mp4",
            source_path="/tmp/video.mp4",
            duration=0.0,
        )

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] send kind=video target=group-1 component_types=['Video'] "
            "payload_file='file:///tmp/video.mp4' source_path='/tmp/video.mp4' duration=0.000s"
        )

    def test_log_video_dispatch_prepared_logs_when_debug_enabled(self, sender) -> None:
        sender.config["debug_enabled_default"] = True

        sender._log_policy.log_video_dispatch_prepared(
            source_path="/tmp/source.mp4",
            mapped_path="/mapped/source.mp4",
            file="file:///mapped/source.mp4",
            ext=".mp4",
            file_size=1024,
            mapped_changed=True,
        )

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] Video dispatch prepared: source_path='/tmp/source.mp4', "
            "mapped_path='/mapped/source.mp4', file='file:///mapped/source.mp4', "
            "ext='.mp4', file_size=1024, mapped_changed=True"
        )

    def test_log_special_media_ready_logs_when_debug_enabled(self, sender) -> None:
        sender.config["debug_enabled_default"] = True

        sender._log_policy.log_special_media_ready(
            target="group-1",
            batch_index=2,
            node_types=["Plain", "Video"],
            source_path=None,
            payload_file="file:///mapped/source.mp4",
            file_size=2048,
            has_plain_text_same_batch=False,
        )

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] Video special media ready: target=group-1, batch_index=2, "
            "node_types=['Plain', 'Video'], source_path=None, "
            "payload_file='file:///mapped/source.mp4', file_size=2048, "
            "has_plain_text_same_batch=False"
        )

    def test_log_merge_send_logs_when_debug_enabled(self, sender) -> None:
        sender.config["debug_enabled_default"] = True

        sender._log_policy.log_merge_send(
            node_name="bot",
            target="group-1",
            label="大合并转发",
            chunk_idx=1,
            total_chunks=3,
            node_count=8,
            batch_count=2,
        )

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] bot -> group-1: 大合并转发 (1/3, 本块 8 节点 / 2 批次)"
        )

    def test_log_audio_split_logs_when_debug_enabled(self, sender) -> None:
        sender.config["debug_enabled_default"] = True

        sender._log_policy.log_audio_split(node_name="bot", target="group-1")

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] bot -> group-1: 单条消息 (音频已拆分补文件)"
        )

    def test_log_target_summary_always_logs(self, sender) -> None:
        sender._log_policy.log_target_summary(
            node_name="bot",
            target="group-1",
            label="单条消息",
        )

        plugin_conftest.mock_logger.info.assert_called_once_with(
            "[QQSender] bot -> group-1: 单条消息"
        )
