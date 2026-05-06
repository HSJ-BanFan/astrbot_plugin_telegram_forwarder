"""Tests for QQSender helper behavior."""

import asyncio
import importlib.util
import shutil
import uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import conftest as plugin_conftest
import pytest


def test_get_platform_bot_returns_none_and_logs_when_get_client_raises(qq_module):
    plugin_conftest.mock_logger.debug.reset_mock()

    class BrokenPlatform:
        def get_client(self):
            raise RuntimeError("broken client")

    assert qq_module.get_platform_bot(BrokenPlatform()) is None
    plugin_conftest.mock_logger.debug.assert_called_once_with(
        "[QQSender] get_client() failed while selecting platform bot: broken client"
    )


@pytest.mark.asyncio
async def test_big_merge_fallback_skips_batches_already_marked_success(qq_module):
    send_calls: list[int] = []

    async def send_processed_batch_fn(**kwargs):
        send_calls.append(kwargs["batch_data"]["batch_index"])

    async def send_message_fn(*args, **kwargs):
        raise RuntimeError("big merge failed")

    processed_batches = [
        {"batch_index": 0, "nodes_data": [[qq_module.Plain("a")]], "contains_audio": False},
        {"batch_index": 1, "nodes_data": [[qq_module.Plain("b")]], "contains_audio": False},
        {"batch_index": 2, "nodes_data": [[qq_module.Plain("c")]], "contains_audio": False},
    ]
    target_successes = {0: set(), 1: {"aiocqhttp:GroupMessage:1"}, 2: set()}
    target_failures: dict[int, str] = {}
    deferred_batch_indexes: set[int] = set()
    lock = asyncio.Lock()

    result = await qq_module.dispatch_processed_batches_to_targets(
        context_target_sessions=["aiocqhttp:GroupMessage:1"],
        real_batches=[[object()], [object()], [object()]],
        processed_batches=processed_batches,
        target_successes=target_successes,
        target_failures=target_failures,
        deferred_batch_indexes=deferred_batch_indexes,
        use_big_merge=True,
        is_mixed_big_merge=False,
        forward_cfg={"qq_merge_chunk_size": 10, "qq_merge_chunk_delay": 0},
        self_id=1,
        node_name="bot",
        get_lock=lambda target: lock,
        target_is_open=lambda target, now_ts: False,
        record_target_success=lambda target: None,
        record_target_failure=lambda target, **kwargs: None,
        classify_send_error=lambda exc: str(exc),
        send_processed_batch_fn=send_processed_batch_fn,
        send_message_fn=send_message_fn,
        fail_fast_limit=10,
        target_circuit_fail_threshold=3,
        target_circuit_cooldown_sec=60,
        log_policy=None,
    )

    assert send_calls == [0, 2]
    assert result.target_successes[1] == {"aiocqhttp:GroupMessage:1"}


class TestDispatchMediaFile:
    def test_dispatch_by_extension(self, sender):
        cases = [
            ("/tmp/photo.jpg", "Image"),
            ("/tmp/photo.png", "Image"),
            ("/tmp/photo.webp", "Image"),
            ("/tmp/photo.gif", "Image"),
            ("/tmp/audio.wav", "Record"),
            ("/tmp/audio.mp3", "Record"),
            ("/tmp/audio.flac", "Record"),
            ("/tmp/audio.ogg", "Record"),
            ("/tmp/video.mp4", "Video"),
            ("/tmp/video.webm", "Video"),
            ("/tmp/video.mkv", "Video"),
            ("/tmp/data.zip", "File"),
            ("/tmp/doc.pdf", "File"),
        ]
        for filename, expected_type in cases:
            result = sender._dispatch_media_file(filename)
            assert len(result) == 1
            assert type(result[0]).__name__ == expected_type

    def test_case_insensitive(self, sender):
        result = sender._dispatch_media_file("/tmp/PHOTO.JPG")
        assert len(result) == 1
        assert type(result[0]).__name__ == "Image"

    def test_video_path_mapping(self, sender):
        sender._map_path = lambda p: "/mapped" + p
        result = sender._dispatch_media_file("/tmp/video.mp4")
        assert len(result) == 1
        assert type(result[0]).__name__ == "Video"
        assert result[0].file == "file:///mapped/tmp/video.mp4"

    def test_video_path_mapping_uses_valid_file_uri_for_posix_path(self, sender):
        sender._map_path = lambda p: "/plugin_data/video.mp4"

        result = sender._dispatch_media_file("/tmp/video.mp4")

        assert len(result) == 1
        assert type(result[0]).__name__ == "Video"
        assert result[0].file == "file:///plugin_data/video.mp4"

    def test_video_path_mapping_uses_file_uri_for_windows_path(self, sender):
        sender._map_path = lambda p: "C:/plugin_data/video.mp4"

        result = sender._dispatch_media_file("D:/tmp/video.mp4")

        assert len(result) == 1
        assert type(result[0]).__name__ == "Video"
        assert result[0].file == "file:///C:/plugin_data/video.mp4"

    def test_video_path_mapping_preserves_existing_uri(self, sender):
        sender._map_path = lambda p: "file:///plugin_data/video.mp4"

        result = sender._dispatch_media_file("/tmp/video.mp4")

        assert len(result) == 1
        assert type(result[0]).__name__ == "Video"
        assert result[0].file == "file:///plugin_data/video.mp4"

    def test_unknown_ext_path_mapping(self, sender):
        sender._map_path = lambda p: "/mapped" + p
        result = sender._dispatch_media_file("/tmp/data.zip")
        assert len(result) == 1
        assert type(result[0]).__name__ == "File"

    def test_file_uses_mapped_path_when_mapping_exists(self, sender):
        sender._map_path = lambda p: "/mapped" + p
        result = sender._dispatch_media_file("/tmp/data.zip")
        assert len(result) == 1
        assert type(result[0]).__name__ == "File"
        assert result[0].file == "/mapped/tmp/data.zip"
        assert result[0].url == ""
        assert result[0].name == "data.zip"

    def test_audio_file_only_mode_returns_file(self, sender):
        result = sender._dispatch_media_file("/tmp/audio.ogg", audio_mode="file_only")
        assert len(result) == 1
        assert type(result[0]).__name__ == "File"

    def test_dispatch_media_file_uses_mapped_path_for_generic_file(self, qq_module):
        result = qq_module.dispatch_media_file(
            "/tmp/report.txt",
            map_path=lambda path: "/mapped/report.txt",
        )

        assert len(result) == 1
        assert type(result[0]).__name__ == "File"
        assert result[0].file == "/mapped/report.txt"
        assert result[0].name == "report.txt"

    @pytest.mark.asyncio
    async def test_patch_file_to_dict_uses_object_setattr_for_strict_models(self):
        previous_modules = plugin_conftest._register_mock_package_tree()
        spec = importlib.util.spec_from_file_location(
            "astrbot_plugin_telegram_forwarder.core.senders.qq_media",
            str(Path(plugin_conftest._repo_root) / "core" / "senders" / "qq_media.py"),
        )
        qq_media = importlib.util.module_from_spec(spec)
        qq_media.__package__ = "astrbot_plugin_telegram_forwarder.core.senders"
        try:
            spec.loader.exec_module(qq_media)
        finally:
            plugin_conftest._restore_mock_package_tree(previous_modules)

        class StrictFile:
            def __init__(self):
                object.__setattr__(self, "name", "mapped.apk")
                object.__setattr__(self, "file_", "/plugin_data/demo/mapped.apk")

            def __setattr__(self, name, value):
                if name not in {"name", "file_"}:
                    raise ValueError(f'"StrictFile" object has no field "{name}"')
                object.__setattr__(self, name, value)

        strict_file = StrictFile()

        patched = qq_media._patch_file_to_dict(strict_file)
        payload = await patched.to_dict()

        assert payload == {
            "type": "file",
            "data": {
                "name": "mapped.apk",
                "file": "/plugin_data/demo/mapped.apk",
            },
        }


class TestGetSenderDisplayName:
    def test_post_author_channel(self, sender):
        msg = type("Msg", (), {"post_author": "channel-editor", "sender": None})()
        assert sender._get_sender_display_name(msg) == "channel-editor"

    def test_sender_first_name(self, sender):
        user = type("User", (), {"first_name": "Alice"})()
        msg = type("Msg", (), {"post_author": None, "sender": user})()
        assert sender._get_sender_display_name(msg) == "Alice"

    def test_post_author_takes_priority(self, sender):
        user = type("User", (), {"first_name": "Alice"})()
        msg = type("Msg", (), {"post_author": "channel-editor", "sender": user})()
        assert sender._get_sender_display_name(msg) == "channel-editor"

    def test_no_name_returns_empty(self, sender):
        msg = type("Msg", (), {"post_author": None, "sender": None})()
        assert sender._get_sender_display_name(msg) == ""

    def test_sender_without_first_name(self, sender):
        user = type("User", (), {})()
        msg = type("Msg", (), {"post_author": None, "sender": user})()
        assert sender._get_sender_display_name(msg) == ""

    def test_sender_first_name_none(self, sender):
        user = type("User", (), {"first_name": None})()
        msg = type("Msg", (), {"post_author": None, "sender": user})()
        assert sender._get_sender_display_name(msg) == ""

    def test_channel_title_fallback(self, sender):
        channel = type("Channel", (), {"title": "MyChannel", "username": "mychannel"})()
        msg = type("Msg", (), {"post_author": None, "sender": channel})()
        assert sender._get_sender_display_name(msg) == "MyChannel"

    def test_channel_username_fallback(self, sender):
        channel = type("Channel", (), {"title": None, "username": "mychannel"})()
        msg = type("Msg", (), {"post_author": None, "sender": channel})()
        assert sender._get_sender_display_name(msg) == "mychannel"

    def test_user_username_fallback(self, sender):
        user = type("User", (), {"first_name": None, "username": "john_doe"})()
        msg = type("Msg", (), {"post_author": None, "sender": user})()
        assert sender._get_sender_display_name(msg) == "john_doe"


class TestBatchAudioDetection:
    def test_detects_record_component(self, sender, qq_module):
        plain = qq_module.Plain("hello")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        assert sender._batch_contains_audio([[plain], [record]]) is True

    def test_ignores_non_audio_components(self, sender, qq_module):
        plain = qq_module.Plain("hello")
        image = qq_module.Image.fromFileSystem("/tmp/photo.jpg")
        file_comp = qq_module.File("/tmp/doc.pdf")
        assert sender._batch_contains_audio([[plain], [image, file_comp]]) is False

    def test_file_only_audio_fallback_is_not_treated_as_audio_batch(self, sender):
        file_comp = sender._dispatch_media_file(
            "/tmp/audio.ogg", audio_mode="file_only"
        )[0]
        assert sender._batch_contains_audio([[file_comp]]) is False

    def test_media_helpers_prevent_merging_audio_and_special_media(self, qq_module):
        record = qq_module.Record.fromFileSystem("/tmp/audio.mp3")
        assert qq_module.batch_contains_audio([[record]]) is True

        video = qq_module.Video.fromFileSystem("/tmp/video.mp4")
        batch_data = {
            "nodes_data": [[qq_module.Plain("caption")], [video]],
            "contains_audio": False,
        }
        assert qq_module.should_merge_batch_nodes(batch_data) is False


class TestBatchMergeDecision:
    def test_audio_batch_never_uses_nodes_merge(self, sender):
        batch_data = {
            "nodes_data": [["text"], ["record"]],
            "contains_audio": True,
        }
        assert sender._should_merge_batch_nodes(batch_data) is False

    def test_non_audio_multi_node_batch_can_use_nodes_merge(self, sender):
        batch_data = {
            "nodes_data": [["text"], ["image"]],
            "contains_audio": False,
        }
        assert sender._should_merge_batch_nodes(batch_data) is True


class TestTargetCircuitHelpers:
    def test_circuit_helpers_open_after_threshold_and_clear_on_success(self, qq_module):
        circuit_state = {}
        target_session = "p:GroupMessage:1"

        qq_module.record_target_failure(
            circuit_state,
            target_session,
            threshold=2,
            cooldown_sec=30,
            now_ts=100.0,
        )
        assert qq_module.target_is_open(circuit_state, target_session, 100.0) is False

        qq_module.record_target_failure(
            circuit_state,
            target_session,
            threshold=2,
            cooldown_sec=30,
            now_ts=101.0,
        )
        assert qq_module.target_is_open(circuit_state, target_session, 102.0) is True

        qq_module.record_target_success(circuit_state, target_session)
        assert qq_module.target_is_open(circuit_state, target_session, 102.0) is False

    def test_circuit_helper_removes_expired_open_state(self, qq_module):
        circuit_state = {
            "p:GroupMessage:1": {
                "consecutive_failures": 2,
                "open_until": 120.0,
            }
        }

        assert (
            qq_module.target_is_open(circuit_state, "p:GroupMessage:1", 121.0) is False
        )
        assert circuit_state == {}


class TestReplyPreview:
    def test_reply_preview_helpers_format_text_preview(self, qq_module):
        user = type("User", (), {"first_name": "Alice"})()
        msg = type(
            "Msg",
            (),
            {
                "sender": user,
                "post_author": None,
                "text": "first line\nsecond line",
            },
        )()

        preview = qq_module.build_reply_preview(msg)

        assert preview == "↩ 回复 Alice:\nfirst line second line"

    def test_reply_preview_helpers_fall_back_to_media_label(self, qq_module):
        msg = type(
            "Msg",
            (),
            {
                "sender": None,
                "post_author": "Channel Author",
                "text": "",
                "photo": object(),
            },
        )()

        preview = qq_module.build_reply_preview(msg)

        assert preview == "↩ 回复 Channel Author:\n[图片]"

    def test_reply_media_label_variants(self, sender):
        assert (
            sender._reply_media_label(type("Msg", (), {"photo": object()})())
            == "[图片]"
        )
        assert (
            sender._reply_media_label(type("Msg", (), {"video": object()})())
            == "[视频]"
        )
        assert (
            sender._reply_media_label(type("Msg", (), {"audio": object()})())
            == "[音频]"
        )
        assert (
            sender._reply_media_label(type("Msg", (), {"document": object()})())
            == "[文件]"
        )
        assert sender._reply_media_label(type("Msg", (), {})()) == "[消息]"

    def test_build_reply_preview_text(self, sender):
        user = type("User", (), {"first_name": "Alice"})()
        msg = type(
            "Msg", (), {"sender": user, "post_author": None, "text": "hello\nworld"}
        )()
        assert sender._build_reply_preview(msg) == "↩ 回复 Alice:\nhello world"

    def test_build_reply_preview_truncates_long_text(self, sender):
        msg = type(
            "Msg", (), {"sender": None, "post_author": None, "text": "x" * 120}
        )()
        result = sender._build_reply_preview(msg)
        assert result.startswith("↩ 回复:\n")
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_prefetch_reply_previews_skips_existing_batch_message(self, sender):
        source = type("Msg", (), {"id": 1, "reply_to": None})()
        reply_header = type("Reply", (), {"reply_to_msg_id": 1})()
        reply_msg = type("Msg", (), {"id": 2, "reply_to": reply_header})()
        sender.downloader.client = MagicMock()
        sender.downloader.client.get_messages = AsyncMock()

        result = await sender._prefetch_reply_previews([source, reply_msg], "demo")

        assert result == {}
        sender.downloader.client.get_messages.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prefetch_reply_previews_fetches_missing_reply(self, sender):
        reply_header = type("Reply", (), {"reply_to_msg_id": 99})()
        msg = type("Msg", (), {"id": 2, "reply_to": reply_header})()
        quoted = type(
            "Msg",
            (),
            {"id": 99, "sender": None, "post_author": None, "text": "quoted text"},
        )()
        sender.downloader.client = MagicMock()
        sender.downloader.client.get_messages = AsyncMock(return_value=[quoted])

        result = await sender._prefetch_reply_previews([msg], "demo")

        assert result == {99: "↩ 回复:\nquoted text"}
        sender.downloader.client.get_messages.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_prefetch_reply_previews_ignores_fetch_failure(self, sender):
        reply_header = type("Reply", (), {"reply_to_msg_id": 99})()
        msg = type("Msg", (), {"id": 2, "reply_to": reply_header})()
        sender.downloader.client = MagicMock()
        sender.downloader.client.get_messages = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        result = await sender._prefetch_reply_previews([msg], "demo")

        assert result == {}


class TestAudioBatchSending:
    @pytest.mark.asyncio
    async def test_send_processed_batch_wrapper_delegates_to_dispatcher(
        self, sender, qq_module
    ):
        dispatcher_mock = AsyncMock()
        setattr(qq_module, "send_processed_batch", dispatcher_mock)

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[qq_module.Plain("x")]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        dispatcher_mock.assert_awaited_once()
        assert dispatcher_mock.await_args.kwargs["allow_forward_nodes"] is True

    @pytest.mark.asyncio
    async def test_captioned_audio_batch_sends_caption_record_then_file(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        plain = qq_module.Plain("caption")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        record.file = "file:////tmp/audio.ogg"
        sender._map_path = lambda p: p

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[plain], [record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 3
        assert calls[0].args[1].chain[0].text == "caption"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"
        assert type(calls[2].args[1].chain[0]).__name__ == "File"

    @pytest.mark.asyncio
    async def test_captioned_audio_batch_sends_source_file_after_record_failure(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[None, RuntimeError("record failed"), None]
        )
        plain = qq_module.Plain("caption")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        sender._map_path = lambda p: p

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[plain], [record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 3
        assert calls[0].args[1].chain[0].text == "caption"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"
        assert type(calls[2].args[1].chain[0]).__name__ == "File"

    @pytest.mark.asyncio
    async def test_captioned_audio_batch_sends_source_file_after_record_timeout(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[None, asyncio.TimeoutError(), None]
        )
        plain = qq_module.Plain("caption")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        sender._map_path = lambda p: p

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[plain], [record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 3
        assert calls[0].args[1].chain[0].text == "caption"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"
        assert type(calls[2].args[1].chain[0]).__name__ == "File"

    @pytest.mark.asyncio
    async def test_captioned_audio_batch_reraises_record_failure_without_source_path(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[None, RuntimeError("record failed")]
        )
        plain = qq_module.Plain("caption")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = None

        with pytest.raises(RuntimeError, match="record failed"):
            await sender._send_processed_batch(
                batch_data={
                    "nodes_data": [[plain], [record]],
                    "contains_audio": True,
                },
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 2
        assert calls[0].args[1].chain[0].text == "caption"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"

    @pytest.mark.asyncio
    async def test_captioned_audio_batch_sends_caption_and_record_when_file_fails(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[None, None, RuntimeError("file failed")]
        )
        qq_module.logger.error.reset_mock()
        plain = qq_module.Plain("caption")
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        sender._map_path = lambda p: p

        with pytest.raises(RuntimeError, match="file failed"):
            await sender._send_processed_batch(
                batch_data={
                    "nodes_data": [[plain], [record]],
                    "contains_audio": True,
                },
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 3
        assert calls[0].args[1].chain[0].text == "caption"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"
        assert type(calls[2].args[1].chain[0]).__name__ == "File"
        error_messages = [
            call.args[0]
            for call in qq_module.logger.error.call_args_list
            if call.args
        ]
        assert any(
            "音频源文件补发失败" in message
            and "error_type=RuntimeError" in message
            and "file failed" in message
            for message in error_messages
        )

    @pytest.mark.asyncio
    async def test_file_send_logs_final_payload_with_file_file__and_url(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        qq_module.logger.info.reset_mock()
        file_component = qq_module.File(file="/tmp/audio.ogg", name="audio.ogg")
        file_component.file_ = "/tmp/audio.ogg"
        file_component.url = ""

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[file_component]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert sender.context.send_message.await_count == 1
        qq_module.logger.info.assert_any_call(
            "[QQSender] File payload -> target: file='/tmp/audio.ogg', file_='/tmp/audio.ogg', url='', name='audio.ogg'"
        )

    @pytest.mark.asyncio
    async def test_file_send_logs_failure_diagnostics_with_batch_index_and_source_path(
        self, sender, qq_module
    ):
        sender.config = {"forward_config": {"apk_fallback_mode": "off"}}
        sender.context.send_message = AsyncMock(
            side_effect=RuntimeError("rich media transfer failed")
        )
        qq_module.logger.warning.reset_mock()
        file_component = qq_module.File(file="/tmp/base.apk", name="base.apk")
        file_component.file_ = "/tmp/base.apk"
        file_component.url = ""
        file_component._tgf_source_path = "/tmp/source/base.apk"

        with pytest.raises(RuntimeError, match="rich media transfer failed"):
            await sender._send_processed_batch(
                batch_data={
                    "batch_index": 0,
                    "nodes_data": [[file_component]],
                    "contains_audio": False,
                    "local_files": [],
                },
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

        warning_messages = [
            call.args[0]
            for call in qq_module.logger.warning.call_args_list
            if call.args
        ]
        assert any(
            "batch_index=0" in message
            and "node_types=['File']" in message
            and "type=File" in message
            and "file='/tmp/base.apk'" in message
            and "file_='/tmp/base.apk'" in message
            and "url=''" in message
            and "name='base.apk'" in message
            and "source_path='/tmp/source/base.apk'" in message
            for message in warning_messages
        )

    @pytest.mark.asyncio
    async def test_file_send_copies_file_path_from_file_compat_field(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        file_component = qq_module.File(name="audio.ogg")
        file_component.file = ""
        file_component.file_ = "/mapped/audio.ogg"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[file_component]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        sent_file = sender.context.send_message.await_args_list[0].args[1].chain[0]
        assert sent_file.file == "/mapped/audio.ogg"

    @pytest.mark.asyncio
    async def test_file_send_overrides_to_dict_for_nonexistent_mapped_path(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        file_component = qq_module.File(name="mapped.apk")
        file_component.file = ""
        file_component.file_ = "/plugin_data/demo/mapped.apk"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[file_component]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        sent_file = sender.context.send_message.await_args_list[0].args[1].chain[0]
        payload = await sent_file.to_dict()
        assert payload == {
            "type": "file",
            "data": {
                "name": "mapped.apk",
                "file": "/plugin_data/demo/mapped.apk",
            },
        }

    @pytest.mark.asyncio
    async def test_apk_file_send_failure_falls_back_to_direct_link(
        self, sender, qq_module
    ):
        sender.config = {
            "forward_config": {
                "apk_fallback_mode": "link",
                "apk_direct_link_base_url": "https://files.example.com/downloads",
            }
        }
        sender.context.send_message = AsyncMock(
            side_effect=[
                RuntimeError("retcode=1200 rich media transfer failed"),
                None,
            ]
        )
        file_component = qq_module.File(file="/mapped/base.apk", name="base.apk")
        file_component.file_ = "/mapped/base.apk"
        file_component._tgf_source_path = "/tmp/base.apk"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[file_component]],
                "local_files": [],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert sender.context.send_message.await_count == 2
        fallback_chain = sender.context.send_message.await_args_list[1].args[1].chain
        assert len(fallback_chain) == 1
        assert type(fallback_chain[0]).__name__ == "Plain"
        assert "https://files.example.com/downloads/base.apk" in fallback_chain[0].text

    @pytest.mark.asyncio
    async def test_handle_apk_file_send_failure_link_or_zip_falls_back_to_zip_after_link_error(
        self, sender, qq_module
    ):
        root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
        root.mkdir(exist_ok=True)
        plugin_data_dir = root / f"apk-fallback-{uuid.uuid4().hex}"
        plugin_data_dir.mkdir()
        apk_path = plugin_data_dir / "base.apk"
        apk_path.write_bytes(b"apk-binary")

        sender.config = {
            "forward_config": {
                "apk_fallback_mode": "link_or_zip",
                "apk_direct_link_base_url": "https://files.example.com/downloads",
            }
        }
        sender.downloader.plugin_data_dir = str(plugin_data_dir)
        sender.context.send_message = AsyncMock(
            side_effect=[
                RuntimeError("rich media transfer failed"),
                RuntimeError("direct link send failed"),
                None,
            ]
        )
        sender._map_path = lambda path: path
        file_component = qq_module.File(file=str(apk_path), name="base.apk")
        file_component.file_ = str(apk_path)
        file_component._tgf_source_path = str(apk_path)
        batch_data = {
            "nodes_data": [[file_component]],
            "local_files": [str(apk_path)],
            "contains_audio": False,
        }

        try:
            await sender._send_processed_batch(
                batch_data=batch_data,
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

            assert sender.context.send_message.await_count == 3
            sent_component = sender.context.send_message.await_args_list[2].args[1].chain[0]
            assert type(sent_component).__name__ == "File"
            assert sent_component.name == "base.apk.zip"
            assert len(batch_data["local_files"]) == 2

            zip_path = Path(batch_data["local_files"][1])
            assert zip_path.is_file()
            with zipfile.ZipFile(zip_path) as archive:
                assert archive.namelist() == ["base.apk"]
                assert archive.read("base.apk") == b"apk-binary"
        finally:
            shutil.rmtree(plugin_data_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_apk_file_send_failure_falls_back_to_zip_archive(
        self, sender, qq_module
    ):
        root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
        root.mkdir(exist_ok=True)
        plugin_data_dir = root / f"apk-fallback-{uuid.uuid4().hex}"
        plugin_data_dir.mkdir()
        apk_path = plugin_data_dir / "base.apk"
        apk_path.write_bytes(b"apk-binary")

        sender.config = {
            "forward_config": {
                "apk_fallback_mode": "zip",
                "apk_direct_link_base_url": "",
            }
        }
        sender.downloader.plugin_data_dir = str(plugin_data_dir)
        sender.context.send_message = AsyncMock(
            side_effect=[
                RuntimeError("rich media transfer failed"),
                None,
            ]
        )
        sender._map_path = lambda path: path
        file_component = qq_module.File(file=str(apk_path), name="base.apk")
        file_component.file_ = str(apk_path)
        file_component._tgf_source_path = str(apk_path)
        batch_data = {
            "nodes_data": [[file_component]],
            "local_files": [str(apk_path)],
            "contains_audio": False,
        }

        try:
            await sender._send_processed_batch(
                batch_data=batch_data,
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

            assert sender.context.send_message.await_count == 2
            sent_component = (
                sender.context.send_message.await_args_list[1].args[1].chain[0]
            )
            assert type(sent_component).__name__ == "File"
            assert sent_component.name == "base.apk.zip"
            assert len(batch_data["local_files"]) == 2

            zip_path = Path(batch_data["local_files"][1])
            assert zip_path.is_file()
            with zipfile.ZipFile(zip_path) as archive:
                assert archive.namelist() == ["base.apk"]
                assert archive.read("base.apk") == b"apk-binary"
        finally:
            shutil.rmtree(plugin_data_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_audio_batch_normalizes_file_component_from_file_compat_field(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        sender._map_path = lambda p: p
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        file_component = qq_module.File(name="extra.ogg")
        file_component.file = ""
        file_component.file_ = "/mapped/extra.ogg"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[record, file_component]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert sender.context.send_message.await_count == 3
        sent_file = sender.context.send_message.await_args_list[2].args[1].chain[0]
        assert sent_file.file == "/mapped/extra.ogg"

    @pytest.mark.asyncio
    async def test_audio_batch_logs_file_payload_with_file_file__and_url(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        qq_module.logger.info.reset_mock()
        file_component = qq_module.File(
            name="audio.ogg", file="/tmp/audio.ogg", url="/mapped/audio.ogg"
        )
        file_component.file_ = "/tmp/audio.ogg"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[file_component]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        qq_module.logger.info.assert_any_call(
            "[QQSender] File payload -> target: file='/tmp/audio.ogg', file_='/tmp/audio.ogg', url='/mapped/audio.ogg', name='audio.ogg'"
        )

    @pytest.mark.asyncio
    async def test_audio_record_with_mapped_file_sends_record_and_file(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        record.file = "file:////tmp/audio.ogg"
        sender._map_path = lambda p: "/mapped/audio.ogg"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert sender.context.send_message.await_count == 2
        file_component = sender.context.send_message.await_args_list[1].args[1].chain[0]
        assert type(file_component).__name__ == "File"
        assert file_component.file == "/mapped/audio.ogg"
        assert file_component.url == ""

    @pytest.mark.asyncio
    async def test_audio_record_without_mapping_still_sends_file_fallback(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        record.file = "file:////tmp/audio.ogg"
        sender._map_path = lambda p: p

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert sender.context.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_audio_record_timeout_log_includes_exception_type(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(side_effect=[asyncio.TimeoutError(), None])
        qq_module.logger.warning.reset_mock()
        record = qq_module.Record.fromFileSystem("/tmp/audio.ogg")
        record.path = "/tmp/audio.ogg"
        record.file = "file:////tmp/audio.ogg"
        sender._map_path = lambda p: p

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[record]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        warning_messages = [
            call.args[0]
            for call in qq_module.logger.warning.call_args_list
            if call.args and "语音条发送失败" in call.args[0]
        ]
        assert any("error_type=TimeoutError" in message for message in warning_messages)

    @pytest.mark.asyncio
    async def test_audio_batch_sends_caption_record_file_for_each_pair(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        sender._map_path = lambda p: p
        plain_a = qq_module.Plain("caption-a")
        record_a = qq_module.Record.fromFileSystem("/tmp/a.ogg")
        record_a.path = "/tmp/a.ogg"
        plain_b = qq_module.Plain("caption-b")
        record_b = qq_module.Record.fromFileSystem("/tmp/b.ogg")
        record_b.path = "/tmp/b.ogg"

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[plain_a], [record_a], [plain_b], [record_b]],
                "contains_audio": True,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 6
        assert calls[0].args[1].chain[0].text == "caption-a"
        assert type(calls[1].args[1].chain[0]).__name__ == "Record"
        assert type(calls[2].args[1].chain[0]).__name__ == "File"
        assert calls[3].args[1].chain[0].text == "caption-b"
        assert type(calls[4].args[1].chain[0]).__name__ == "Record"
        assert type(calls[5].args[1].chain[0]).__name__ == "File"


class TestQQSendWrapper:
    @pytest.mark.asyncio
    async def test_send_with_timeout_logs_send_kind_and_duration(
        self, sender, qq_module
    ):
        sender.config["debug_enabled_default"] = True
        sender.context.send_message = AsyncMock()
        qq_module.logger.info.reset_mock()
        message_chain = MagicMock(name="message_chain")

        await sender._send_with_timeout(
            unified_msg_origin="target",
            message_chain=message_chain,
            send_kind="plain",
            timeout_sec=5.0,
        )

        sender.context.send_message.assert_awaited_once_with("target", message_chain)
        assert any(
            "send kind=plain" in call.args[0] and "duration=" in call.args[0]
            for call in qq_module.logger.info.call_args_list
            if call.args
        )

    @pytest.mark.asyncio
    async def test_send_with_timeout_raises_timeout_for_slow_send(
        self, sender, qq_module
    ):
        qq_module.logger.info.reset_mock()

        async def slow_send(*args, **kwargs):
            await asyncio.sleep(1)

        sender.context.send_message = slow_send
        message_chain = MagicMock(name="message_chain")

        with pytest.raises(asyncio.TimeoutError):
            await sender._send_with_timeout(
                unified_msg_origin="target",
                message_chain=message_chain,
                send_kind="plain",
                timeout_sec=0.01,
            )

    @pytest.mark.asyncio
    async def test_send_processed_batch_logs_special_media_send_kind(
        self, sender, qq_module
    ):
        sender.config["debug_enabled_default"] = True
        sender.context.send_message = AsyncMock()
        qq_module.logger.info.reset_mock()
        file_component = qq_module.File(file="/tmp/base.apk", name="base.apk")
        file_component.file_ = "/tmp/base.apk"
        file_component.url = ""

        await sender._send_processed_batch(
            batch_data={
                "batch_index": 0,
                "nodes_data": [[file_component]],
                "contains_audio": False,
                "local_files": [],
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        assert any(
            "send kind=special_media" in call.args[0] and "duration=" in call.args[0]
            for call in qq_module.logger.info.call_args_list
            if call.args
        )


class TestVideoBatchSending:
    @pytest.mark.asyncio
    async def test_video_batch_sends_source_file_after_video_failure(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[RuntimeError("rich media transfer failed"), None]
        )
        sender._map_path = lambda path: path.replace("/tmp", "/mapped/tmp", 1)
        video = qq_module.Video.fromFileSystem("/tmp/video.mp4")
        video.path = "/tmp/video.mp4"

        await sender._send_processed_batch(
            batch_data={
                "batch_index": 0,
                "nodes_data": [[video]],
                "contains_audio": False,
                "local_files": [],
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 2
        assert type(calls[0].args[1].chain[0]).__name__ == "Video"
        assert type(calls[1].args[1].chain[0]).__name__ == "File"
        assert calls[1].args[1].chain[0].file == "/mapped/tmp/video.mp4"
        assert calls[1].args[1].chain[0].name == "video.mp4"

        warning_messages = [
            call.args[0]
            for call in qq_module.logger.warning.call_args_list
            if call.args
        ]
        assert any(
            "Special media send failed" in message
            and "type=Video" in message
            and "error_type=RuntimeError" in message
            for message in warning_messages
        )
        assert any(
            "视频发送失败，继续发送源文件" in message
            and "target=target" in message
            and "error_type=RuntimeError" in message
            and "rich media transfer failed" in message
            for message in warning_messages
        )

    @pytest.mark.asyncio
    async def test_video_batch_reraises_video_failure_without_source_path(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=RuntimeError("rich media transfer failed")
        )
        video = qq_module.Video(file="")
        video.path = None

        with pytest.raises(RuntimeError, match="rich media transfer failed"):
            await sender._send_processed_batch(
                batch_data={
                    "batch_index": 0,
                    "nodes_data": [[video]],
                    "contains_audio": False,
                    "local_files": [],
                },
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 1
        assert type(calls[0].args[1].chain[0]).__name__ == "Video"

    @pytest.mark.asyncio
    async def test_video_batch_reraises_when_fallback_file_send_fails(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock(
            side_effect=[
                RuntimeError("rich media transfer failed"),
                RuntimeError("file failed"),
            ]
        )
        qq_module.logger.error.reset_mock()
        sender._map_path = lambda path: path.replace("/tmp", "/mapped/tmp", 1)
        video = qq_module.Video.fromFileSystem("/tmp/video.mp4")
        video.path = "/tmp/video.mp4"

        with pytest.raises(RuntimeError, match="file failed"):
            await sender._send_processed_batch(
                batch_data={
                    "batch_index": 0,
                    "nodes_data": [[video]],
                    "contains_audio": False,
                    "local_files": [],
                },
                unified_msg_origin="target",
                self_id=1,
                node_name="bot",
                target_session="target",
            )

        calls = sender.context.send_message.await_args_list
        assert len(calls) == 2
        assert type(calls[0].args[1].chain[0]).__name__ == "Video"
        assert type(calls[1].args[1].chain[0]).__name__ == "File"
        error_messages = [
            call.args[0]
            for call in qq_module.logger.error.call_args_list
            if call.args
        ]
        assert any(
            "视频源文件补发失败" in message
            and "error_type=RuntimeError" in message
            and "file failed" in message
            for message in error_messages
        )


class TestQQBatchBuilder:
    @pytest.mark.asyncio
    async def test_build_processed_batches_preserves_header_text_media_and_files(
        self, sender, qq_module
    ):
        sender.downloader.download_media = AsyncMock(return_value=["/tmp/photo.jpg"])
        sender._dispatch_media_file = lambda path: [
            qq_module.Image.fromFileSystem(path)
        ]
        msg = type(
            "Msg", (), {"id": 1, "text": "hello", "reply_to": None, "_max_file_size": 0}
        )()

        result = await qq_module.build_processed_batches(
            sender=sender,
            real_batches=[[msg]],
            src_channel="demo",
            display_name="demo",
            involved_channels=None,
            strip_links=False,
            exclude_text_on_media=False,
        )

        assert len(result.processed_batches) == 1
        batch = result.processed_batches[0]
        assert batch["batch_index"] == 0
        assert batch["local_files"] == ["/tmp/photo.jpg"]
        assert batch["contains_audio"] is False
        assert any(
            hasattr(component, "text") and "From @demo:" in component.text
            for node in batch["nodes_data"]
            for component in node
        )
        assert result.target_failures == {}


class TestReplyPreviewIntegration:
    @pytest.mark.asyncio
    async def test_reply_preview_prepended_before_message_text(self, sender, qq_module):
        sender.context.send_message = AsyncMock()
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender.downloader.client = MagicMock()
        quoted = type(
            "Msg",
            (),
            {"id": 99, "sender": None, "post_author": None, "text": "quoted text"},
        )()
        sender.downloader.client.get_messages = AsyncMock(return_value=[quoted])
        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        reply_header = type("Reply", (), {"reply_to_msg_id": 99})()
        msg = type(
            "Msg", (), {"id": 2, "text": "reply body", "reply_to": reply_header}
        )()

        await sender.send(
            batches=[[msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
        )

        sent_chain = sender.context.send_message.await_args_list[0].args[1]
        texts = [
            component.text
            for component in sent_chain.chain
            if hasattr(component, "text")
        ]
        assert any("↩ 回复:" in text for text in texts)
        assert any("quoted text" in text for text in texts)
        assert any("reply body" in text for text in texts)


class TestSendHelperExtraction:
    def test_resolve_text_processing_options_uses_global_config_for_mixed_channels(
        self, sender
    ):
        sender.config = {
            "forward_config": {
                "strip_markdown_links": True,
                "exclude_text_on_media": False,
            }
        }
        effective_cfg = {
            "strip_markdown_links": False,
            "exclude_text_on_media": True,
        }

        strip_links, exclude_text_on_media = sender._resolve_text_processing_options(
            effective_cfg, ["channel-a", "channel-b"]
        )

        assert strip_links is True
        assert exclude_text_on_media is False

    def test_resolve_text_processing_options_uses_effective_config_for_single_channel(
        self, sender
    ):
        sender.config = {
            "forward_config": {
                "strip_markdown_links": True,
                "exclude_text_on_media": False,
            }
        }
        effective_cfg = {
            "strip_markdown_links": False,
            "exclude_text_on_media": True,
        }

        strip_links, exclude_text_on_media = sender._resolve_text_processing_options(
            effective_cfg, ["channel-a"]
        )

        assert strip_links is False
        assert exclude_text_on_media is True

    def test_module_export_resolve_text_processing_options_for_mixed_channels(
        self, qq_module
    ):
        config = {
            "forward_config": {
                "strip_markdown_links": True,
                "exclude_text_on_media": False,
            }
        }
        effective_cfg = {
            "strip_markdown_links": False,
            "exclude_text_on_media": True,
        }

        strip_links, exclude_text_on_media = qq_module.resolve_text_processing_options(
            config,
            effective_cfg,
            ["channel-a", "channel-b"],
        )

        assert strip_links is True
        assert exclude_text_on_media is False

    def test_module_export_resolve_send_limits(self, qq_module):
        assert qq_module.resolve_send_limits(
            {
                "qq_target_fail_fast_consecutive_failures": "0",
                "target_circuit_fail_threshold": "2",
                "target_circuit_cooldown_sec": None,
            }
        ) == (1, 2, 300)

    def test_module_export_flatten_batches(self, qq_module):
        batches = [["a"], [["b"], ["c"]], ["d"]]

        assert qq_module.flatten_batches(batches) == [["a"], ["b"], ["c"], ["d"]]

    def test_module_export_build_send_summary_preserves_ack_failed_deferred_semantics(
        self, qq_module
    ):
        summary = qq_module.build_send_summary(
            qq_module.QQSendSummary,
            context_target_sessions=["test:GroupMessage:1", "test:GroupMessage:2"],
            target_successes={
                0: {"test:GroupMessage:1", "test:GroupMessage:2"},
                1: {"test:GroupMessage:1"},
                2: set(),
            },
            target_failures={1: "timeout", 2: "circuit_open"},
            deferred_batch_indexes={2},
        )

        assert isinstance(summary, qq_module.QQSendSummary)
        assert summary.acked_batch_indexes == (0,)
        assert summary.failed_batch_indexes == (1,)
        assert summary.deferred_batch_indexes == (2,)
        assert summary.error_types == {1: "timeout", 2: "circuit_open"}

    def test_module_export_collect_processed_batch_local_files(self, qq_module):
        processed_batches = [
            {
                "batch_index": 0,
                "nodes_data": [],
                "local_files": ["/tmp/a.jpg", "/tmp/b.mp4"],
                "contains_audio": False,
            },
            {
                "batch_index": 1,
                "nodes_data": [],
                "local_files": ["/tmp/c.ogg"],
                "contains_audio": True,
            },
            {
                "batch_index": 2,
                "nodes_data": [],
                "local_files": [],
                "contains_audio": False,
            },
        ]

        assert qq_module.collect_processed_batch_local_files(processed_batches) == [
            "/tmp/a.jpg",
            "/tmp/b.mp4",
            "/tmp/c.ogg",
        ]


class TestDispatchLoopExtraction:
    @pytest.mark.asyncio
    async def test_send_cleans_up_processed_batches_when_dispatch_raises(self, sender, qq_module, monkeypatch):
        cleanup_calls: list[list[dict]] = []
        processed_batch = {
            "batch_index": 0,
            "nodes_data": [[qq_module.Plain("hello")]],
            "local_files": ["/tmp/a.txt"],
            "contains_audio": False,
        }

        async def fake_dispatch(**_kwargs):
            raise RuntimeError("dispatch failed")

        monkeypatch.setattr(
            qq_module,
            "build_processed_batches",
            AsyncMock(
                return_value=type(
                    "BuildResult",
                    (),
                    {"processed_batches": [processed_batch], "target_failures": {}},
                )()
            ),
        )
        monkeypatch.setattr(
            qq_module,
            "dispatch_processed_batches_to_targets",
            fake_dispatch,
        )
        monkeypatch.setattr(
            sender,
            "_cleanup_processed_batches",
            lambda processed_batches: cleanup_calls.append(processed_batches),
        )

        with pytest.raises(RuntimeError, match="dispatch failed"):
            await sender.send(
                batches=[[type("Msg", (), {"id": 1, "text": "hello", "reply_to": None})()]],
                src_channel="demo",
                effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            )

        assert len(cleanup_calls) == 1
        assert cleanup_calls[0] == [processed_batch]

    @pytest.mark.asyncio
    async def test_send_delegates_target_dispatch_to_dispatcher_function(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.client = MagicMock()
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender._cleanup_files = MagicMock()

        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        processed_batch = {
            "batch_index": 0,
            "nodes_data": [[qq_module.Plain("hello")]],
            "local_files": ["/tmp/a.txt"],
            "contains_audio": False,
        }
        qq_module.build_processed_batches = AsyncMock(
            return_value=type(
                "BuildResult",
                (),
                {"processed_batches": [processed_batch], "target_failures": {}},
            )()
        )
        dispatch_mock = AsyncMock(
            return_value=type(
                "DispatchResult",
                (),
                {
                    "target_successes": {0: {"test:GroupMessage:1"}},
                    "target_failures": {},
                    "deferred_batch_indexes": set(),
                },
            )()
        )
        setattr(qq_module, "dispatch_processed_batches_to_targets", dispatch_mock)

        summary = await sender.send(
            batches=[[type("Msg", (), {"id": 1, "text": "x", "reply_to": None})()]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        dispatch_mock.assert_awaited_once()
        sender._cleanup_files.assert_called_once_with(["/tmp/a.txt"])
        assert summary.acked_batch_indexes == (0,)
        assert summary.failed_batch_indexes == ()
        assert summary.deferred_batch_indexes == ()


class TestSendSummary:
    @pytest.mark.asyncio
    async def test_send_returns_batch_indexes_acked_only_when_all_targets_succeed(
        self, sender
    ):
        async def send_message(*args, **kwargs):
            if args[0] == "test:GroupMessage:2":
                raise RuntimeError("WebSocket API call timeout")
            return None

        sender.context.send_message = AsyncMock(side_effect=send_message)
        sender.config = {"forward_config": {"qq_merge_threshold": 99}}
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.client = MagicMock()
        sender.downloader.download_media = AsyncMock(return_value=[])
        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        msg1 = type("Msg", (), {"id": 1, "text": "a", "reply_to": None})()
        msg2 = type("Msg", (), {"id": 2, "text": "b", "reply_to": None})()

        summary = await sender.send(
            batches=[[msg1], [msg2]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={
                "effective_target_qq_sessions": [
                    "test:GroupMessage:1",
                    "test:GroupMessage:2",
                ]
            },
            involved_channels=None,
        )

        assert summary.acked_batch_indexes == ()
        assert set(summary.failed_batch_indexes) == {0, 1}
        assert summary.error_types == {0: "timeout", 1: "timeout"}

    @pytest.mark.asyncio
    async def test_send_marks_preprocess_empty_batch_with_error_type(self, sender):
        sender.context.send_message = AsyncMock()
        sender.config = {"forward_config": {"qq_merge_threshold": 99}}
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.client = MagicMock()
        sender.downloader.download_media = AsyncMock(return_value=[])
        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        msg = type("Msg", (), {"id": 3, "text": "", "reply_to": None})()

        summary = await sender.send(
            batches=[[msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        assert summary.acked_batch_indexes == ()
        assert summary.failed_batch_indexes == (0,)
        assert summary.error_types == {0: "preprocess_empty"}


class TestTargetCircuitBreaker:
    @staticmethod
    def _make_msg(msg_id, text=None):
        return type("Msg", (), {"id": msg_id, "text": text, "reply_to": None})()

    @pytest.mark.asyncio
    async def test_timeout_opens_target_circuit_until_cooldown_expires(self, sender):
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender.downloader.client = MagicMock()

        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        sender.config = {
            "forward_config": {
                "qq_merge_threshold": 99,
                "target_circuit_fail_threshold": 3,
                "target_circuit_cooldown_sec": 300,
            }
        }

        timeout_error = RuntimeError("WebSocket API call timeout")
        sender.context.send_message = AsyncMock(side_effect=timeout_error)

        batches = [[self._make_msg(1, "m1")]]
        for _ in range(3):
            await sender.send(
                batches=batches,
                src_channel="demo",
                display_name="demo",
                effective_cfg={
                    "effective_target_qq_sessions": ["test:GroupMessage:cb"]
                },
            )

        sender.context.send_message = AsyncMock()
        summary = await sender.send(
            batches=batches,
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:cb"]},
        )

        sender.context.send_message.assert_not_awaited()
        assert summary.deferred_batch_indexes == (0,)


class TestCleanupFiles:
    def test_cleanup_processed_batches_does_not_remove_untracked_original_file(self, sender, qq_module, tmp_path):
        temp_file = tmp_path / "fallback.zip"
        original_file = tmp_path / "original.mp4"
        temp_file.write_bytes(b"zip")
        original_file.write_bytes(b"video")
        sender.plugin_data_dir = str(tmp_path)

        processed_batches = [
            {
                "local_files": [str(temp_file)],
                "nodes_data": [[qq_module.File(file=str(original_file), name="original.mp4")]],
            }
        ]

        sender._cleanup_processed_batches(processed_batches)

        assert not temp_file.exists()
        assert original_file.exists()

    def test_cleanup_files_removes_only_files_under_plugin_data_dir(self, sender):
        root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
        root.mkdir(exist_ok=True)
        plugin_data_dir = root / f"qq-cleanup-{uuid.uuid4().hex}"
        plugin_data_dir.mkdir()
        safe_file = plugin_data_dir / "download.bin"
        unsafe_file = root / f"unsafe-{plugin_data_dir.name}.bin"
        safe_file.write_text("safe", encoding="utf-8")
        unsafe_file.write_text("unsafe", encoding="utf-8")
        sender.plugin_data_dir = str(plugin_data_dir)

        try:
            sender._cleanup_files([str(safe_file), str(unsafe_file)])

            assert not safe_file.exists()
            assert unsafe_file.exists()
        finally:
            unsafe_file.unlink(missing_ok=True)
            shutil.rmtree(plugin_data_dir, ignore_errors=True)

    def test_cleanup_files_does_not_remove_external_symlink_to_plugin_file(
        self, sender
    ):
        root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
        root.mkdir(exist_ok=True)
        plugin_data_dir = root / f"qq-cleanup-{uuid.uuid4().hex}"
        plugin_data_dir.mkdir()
        safe_file = plugin_data_dir / "download.bin"
        external_link = root / f"external-link-{plugin_data_dir.name}.bin"
        safe_file.write_text("safe", encoding="utf-8")
        sender.plugin_data_dir = str(plugin_data_dir)

        try:
            try:
                external_link.symlink_to(safe_file)
            except OSError as exc:
                pytest.skip(f"symlink creation is unavailable: {exc}")

            sender._cleanup_files([str(external_link)])

            assert external_link.exists()
            assert safe_file.exists()
        finally:
            external_link.unlink(missing_ok=True)
            shutil.rmtree(plugin_data_dir, ignore_errors=True)

    def test_cleanup_files_logs_remove_failure(self, sender, qq_module, monkeypatch):
        root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
        root.mkdir(exist_ok=True)
        plugin_data_dir = root / f"qq-cleanup-{uuid.uuid4().hex}"
        plugin_data_dir.mkdir()
        safe_file = plugin_data_dir / "download.bin"
        safe_file.write_text("safe", encoding="utf-8")
        sender.plugin_data_dir = str(plugin_data_dir)

        remove_error = OSError("locked")
        monkeypatch.setattr("os.remove", MagicMock(side_effect=remove_error))

        try:
            sender._cleanup_files([str(safe_file)])

            qq_module.logger.warning.assert_called()
        finally:
            shutil.rmtree(plugin_data_dir, ignore_errors=True)


class TestTargetLevelFailFast:
    @staticmethod
    def _make_msg(msg_id, text=None):
        return type("Msg", (), {"id": msg_id, "text": text, "reply_to": None})()

    @pytest.mark.asyncio
    async def test_per_batch_fail_fast_stops_single_target_only(self, sender):
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender.downloader.client = MagicMock()

        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        sender.config = {
            "forward_config": {
                "qq_target_fail_fast_consecutive_failures": 2,
            }
        }

        calls_by_target = {"test:GroupMessage:fail": 0, "test:GroupMessage:ok": 0}

        async def fake_send_processed_batch(*, target_session, **kwargs):
            calls_by_target[target_session] += 1
            if target_session == "test:GroupMessage:fail":
                raise RuntimeError("send failed")
            return None

        sender._send_processed_batch = AsyncMock(side_effect=fake_send_processed_batch)

        batches = [
            [self._make_msg(1, "m1")],
            [self._make_msg(2, "m2")],
            [self._make_msg(3, "m3")],
        ]
        summary = await sender.send(
            batches=batches,
            src_channel="demo",
            display_name="demo",
            effective_cfg={
                "effective_target_qq_sessions": [
                    "test:GroupMessage:fail",
                    "test:GroupMessage:ok",
                ]
            },
        )

        assert calls_by_target["test:GroupMessage:fail"] == 2
        assert calls_by_target["test:GroupMessage:ok"] == 3
        assert summary.acked_batch_indexes == ()
        assert summary.failed_batch_indexes == (0, 1, 2)

    @pytest.mark.asyncio
    async def test_big_merge_fail_fast_uses_configured_threshold(self, sender):
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender.downloader.client = MagicMock()
        sender.context.send_message = AsyncMock(
            side_effect=RuntimeError("merge failed")
        )

        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        sender.config = {
            "forward_config": {
                "qq_merge_threshold": 2,
                "qq_merge_chunk_size": 1,
                "qq_merge_chunk_delay": 0,
                "qq_target_fail_fast_consecutive_failures": 1,
            }
        }

        sender._send_processed_batch = AsyncMock(
            side_effect=RuntimeError("fallback failed")
        )

        batches = [
            [self._make_msg(11, "a")],
            [self._make_msg(12, "b")],
            [self._make_msg(13, "c")],
        ]
        summary = await sender.send(
            batches=batches,
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
        )

        assert sender.context.send_message.await_count == 0
        assert sender._send_processed_batch.await_count == 2
        assert summary.acked_batch_indexes == ()
        assert summary.failed_batch_indexes == (0, 1, 2)

    @pytest.mark.asyncio
    async def test_big_merge_recoverable_chunk_does_not_advance_fail_streak(
        self, sender
    ):
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.download_media = AsyncMock(return_value=[])
        sender.downloader.client = MagicMock()

        merge_attempts = {"count": 0}

        async def merge_send(*args, **kwargs):
            merge_attempts["count"] += 1
            if merge_attempts["count"] in {1, 2}:
                raise RuntimeError("merge failed")
            return None

        sender.context.send_message = AsyncMock(side_effect=merge_send)

        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

        sender.config = {
            "forward_config": {
                "qq_merge_threshold": 2,
                "qq_merge_chunk_size": 1,
                "qq_merge_chunk_delay": 0,
                "qq_target_fail_fast_consecutive_failures": 2,
            }
        }

        async def fallback_send(*, batch_data, **kwargs):
            if batch_data["batch_index"] == 1:
                raise RuntimeError("fallback failed")
            return None

        sender._send_processed_batch = AsyncMock(side_effect=fallback_send)

        batches = [
            [self._make_msg(21, "a")],
            [self._make_msg(22, "b")],
            [self._make_msg(23, "c")],
        ]
        summary = await sender.send(
            batches=batches,
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
        )

        assert sender.context.send_message.await_count == 0
        assert sender._send_processed_batch.await_count == 4
        assert summary.acked_batch_indexes == (0, 2)
        assert summary.failed_batch_indexes == (1,)


class TestBigMergeFallback:
    @staticmethod
    def _make_msg(msg_id, text=None):
        return type("Msg", (), {"id": msg_id, "text": text, "reply_to": None})()

    @staticmethod
    def _configure_sender(sender):
        attempts = {"count": 0}

        async def send_message(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("merged-forward boom")
            return None

        sender.context.send_message = AsyncMock(side_effect=send_message)
        sender.config = {"forward_config": {"qq_merge_threshold": 2}}
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.client = MagicMock()
        sender._map_path = lambda path: path
        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot

    @pytest.mark.asyncio
    async def test_big_merge_failure_fallback_uses_plain_messages_instead_of_nodes(
        self, sender
    ):
        self._configure_sender(sender)
        image_a = self._make_msg(1)
        image_b = self._make_msg(2)
        plain_msg = self._make_msg(3, text="tail")

        async def download_media(msg, max_size_mb=0):
            return {
                1: ["/tmp/a.jpg"],
                2: ["/tmp/b.jpg"],
                3: [],
            }[msg.id]

        sender.downloader.download_media = AsyncMock(side_effect=download_media)

        await sender.send(
            batches=[[image_a, image_b], [plain_msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        calls = sender.context.send_message.await_args_list
        assert sender.context.send_message.await_count == 4
        assert all(
            not any(
                type(component).__name__ == "Nodes" for component in call.args[1].chain
            )
            for call in calls[1:]
        )
        assert any(
            [type(component).__name__ for component in call.args[1].chain]
            == ["Plain", "Image"]
            for call in calls[1:]
        )
        assert any(
            [type(component).__name__ for component in call.args[1].chain] == ["Image"]
            for call in calls[1:]
        )

    @pytest.mark.asyncio
    async def test_big_merge_failure_fallback_splits_video_and_file_components(
        self, sender
    ):
        self._configure_sender(sender)
        mixed_media_msg = self._make_msg(1, text="caption")
        plain_msg = self._make_msg(2, text="tail")

        async def download_media(msg, max_size_mb=0):
            return {
                1: ["/tmp/video.mp4", "/tmp/doc.zip"],
                2: [],
            }[msg.id]

        sender.downloader.download_media = AsyncMock(side_effect=download_media)

        await sender.send(
            batches=[[mixed_media_msg], [plain_msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        calls = sender.context.send_message.await_args_list
        assert sender.context.send_message.await_count == 5
        assert any(
            len(call.args[1].chain) == 1
            and type(call.args[1].chain[0]).__name__ == "Video"
            for call in calls[1:]
        )
        assert any(
            len(call.args[1].chain) == 1
            and type(call.args[1].chain[0]).__name__ == "File"
            for call in calls[1:]
        )
        assert all(
            not any(
                type(component).__name__ == "Video" for component in call.args[1].chain
            )
            or len(call.args[1].chain) == 1
            for call in calls[1:]
        )

    @pytest.mark.asyncio
    async def test_big_merge_failure_fallback_keeps_audio_record_and_file_semantics(
        self, sender
    ):
        self._configure_sender(sender)
        audio_msg = self._make_msg(1, text="caption")
        plain_msg = self._make_msg(2, text="tail")

        async def download_media(msg, max_size_mb=0):
            return {
                1: ["/tmp/audio.ogg"],
                2: [],
            }[msg.id]

        sender.downloader.download_media = AsyncMock(side_effect=download_media)

        await sender.send(
            batches=[[audio_msg], [plain_msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        calls = sender.context.send_message.await_args_list
        assert sender.context.send_message.await_count == 5
        assert any(
            len(call.args[1].chain) == 1
            and type(call.args[1].chain[0]).__name__ == "Record"
            for call in calls[1:]
        )
        assert (
            sum(
                1
                for call in calls[1:]
                if len(call.args[1].chain) == 1
                and type(call.args[1].chain[0]).__name__ == "File"
            )
            >= 1
        )

    @pytest.mark.asyncio
    async def test_big_merge_single_node_audio_chunk_keeps_record_and_file_semantics(
        self, sender
    ):
        sender.context.send_message = AsyncMock()
        sender._bootstrap_qq_runtime = AsyncMock()
        sender._ensure_node_name = AsyncMock(return_value="bot")
        sender.downloader.client = MagicMock()
        sender._map_path = lambda path: path
        bot = MagicMock()
        bot.get_login_info = AsyncMock(return_value={"user_id": 1})
        sender.bot = bot
        plain_msg = self._make_msg(1, text="tail")
        audio_msg = self._make_msg(2, text="caption")

        async def download_media(msg, max_size_mb=0):
            return {
                1: [],
                2: ["/tmp/audio.ogg"],
            }[msg.id]

        sender.downloader.download_media = AsyncMock(side_effect=download_media)
        sender.config = {
            "forward_config": {
                "qq_merge_threshold": 2,
                "qq_merge_chunk_size": 1,
                "qq_merge_chunk_delay": 0,
            }
        }

        await sender.send(
            batches=[[plain_msg], [audio_msg]],
            src_channel="demo",
            display_name="demo",
            effective_cfg={"effective_target_qq_sessions": ["test:GroupMessage:1"]},
            involved_channels=None,
        )

        calls = sender.context.send_message.await_args_list
        assert sender.context.send_message.await_count == 4
        assert all(
            not any(
                type(component).__name__ == "Nodes" for component in call.args[1].chain
            )
            for call in calls[1:]
        )
        assert any(
            len(call.args[1].chain) == 1
            and type(call.args[1].chain[0]).__name__ == "Record"
            for call in calls[1:]
        )
        assert any(
            len(call.args[1].chain) == 1
            and type(call.args[1].chain[0]).__name__ == "File"
            for call in calls[1:]
        )

    @pytest.mark.asyncio
    async def test_send_processed_batch_logs_node_component_types(
        self, sender, qq_module
    ):
        sender.context.send_message = AsyncMock()
        qq_module.logger.debug.reset_mock()
        plain = qq_module.Plain("caption")
        image = qq_module.Image.fromFileSystem("/tmp/photo.jpg")

        await sender._send_processed_batch(
            batch_data={
                "nodes_data": [[plain, image]],
                "contains_audio": False,
            },
            unified_msg_origin="target",
            self_id=1,
            node_name="bot",
            target_session="target",
        )

        debug_calls = [call.args[0] for call in qq_module.logger.debug.call_args_list]
        assert any(
            "node_types=" in message
            and "Plain" in message
            and "Image" in message
            and "caption" not in message
            for message in debug_calls
        )


class TestQQTargetHelpers:
    def test_split_qq_targets_separates_sessions_and_numeric_groups(self, qq_module):
        session_targets, group_ids = qq_module.split_qq_targets(
            ["aiocqhttp:GroupMessage:100", "200", 300, "", None, "bad-target"]
        )

        assert session_targets == ["aiocqhttp:GroupMessage:100"]
        assert group_ids == ["200", "300"]

    def test_dedupe_keep_order_preserves_first_occurrence(self, qq_module):
        assert qq_module.dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_session_platform_ids_extracts_unique_platform_ids(self, qq_module):
        assert qq_module.session_platform_ids(
            [
                "aiocqhttp:GroupMessage:1",
                "telegram:GroupMessage:2",
                "aiocqhttp:PrivateMessage:3",
                "invalid",
            ]
        ) == ["aiocqhttp", "telegram"]

    def test_classify_send_error_matches_existing_error_strings(self, qq_module):
        assert (
            qq_module.classify_send_error(RuntimeError("WebSocket API call timeout"))
            == "timeout"
        )
        assert (
            qq_module.classify_send_error(RuntimeError("retcode=1200"))
            == "retcode_1200"
        )
        assert (
            qq_module.classify_send_error(RuntimeError("wrong session ID"))
            == "wrong_session_id"
        )
        assert qq_module.classify_send_error(RuntimeError("other")) == "send_failed"


class TestQQRuntimeHelpers:
    def test_get_platform_instances_reads_platform_insts(self, qq_module):
        platform = MagicMock()
        manager = type("Manager", (), {"platform_insts": [platform]})()
        context = type("Context", (), {"platform_manager": manager})()

        assert qq_module.get_platform_instances(context) == [platform]

    def test_get_platform_instances_uses_get_insts_fallback(self, qq_module):
        platform = MagicMock()
        manager = type("Manager", (), {"get_insts": lambda self: [platform]})()
        context = type("Context", (), {"platform_manager": manager})()

        assert qq_module.get_platform_instances(context) == [platform]

    def test_select_platform_prefers_configured_platform_id(self, qq_module):
        preferred = MagicMock()
        preferred.meta.return_value = type(
            "Meta", (), {"id": "preferred", "name": "other"}
        )()
        fallback = MagicMock()
        fallback.meta.return_value = type(
            "Meta", (), {"id": "fallback", "name": "aiocqhttp"}
        )()

        selected = qq_module.select_qq_platform(
            [fallback, preferred], ["preferred"], None
        )

        assert selected == (preferred, "preferred", "other")

    def test_select_platform_prefers_aiocqhttp_when_no_preference(self, qq_module):
        other = MagicMock()
        other.meta.return_value = type(
            "Meta", (), {"id": "other", "name": "telegram"}
        )()
        qq = MagicMock()
        qq.meta.return_value = type("Meta", (), {"id": "qq", "name": "aiocqhttp"})()

        selected = qq_module.select_qq_platform([other, qq], [], None)

        assert selected == (qq, "qq", "aiocqhttp")

    def test_select_platform_prefers_platform_ids_in_list_order(self, qq_module):
        platform_a = MagicMock()
        platform_a.meta.return_value = type(
            "Meta", (), {"id": "a", "name": "platform-a"}
        )()
        platform_b = MagicMock()
        platform_b.meta.return_value = type(
            "Meta", (), {"id": "b", "name": "platform-b"}
        )()

        selected = qq_module.select_qq_platform(
            [platform_a, platform_b], ["b", "a"], None
        )

        assert selected == (platform_b, "b", "platform-b")


class TestProcessedBatchType:
    def test_processed_batch_can_be_used_as_existing_batch_data_dict(self, qq_module):
        plain = qq_module.Plain("hello")
        batch = qq_module.ProcessedBatch(
            batch_index=3,
            nodes_data=[[plain]],
            local_files=["/tmp/file.txt"],
            contains_audio=False,
        )

        assert batch.as_batch_data() == {
            "batch_index": 3,
            "nodes_data": [[plain]],
            "local_files": ["/tmp/file.txt"],
            "contains_audio": False,
        }
