"""qq_file_fallback 失败回退策略单元测试。

通过 conftest 的包树注册加载该子模块（带相对导入），astrbot/telethon 均已 mock。
"""

import importlib.util
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import conftest as plugin_conftest
import pytest

_repo_root = Path(__file__).resolve().parents[1]


def load_fallback_module():
    previous = plugin_conftest._register_mock_package_tree()
    try:
        full_name = "astrbot_plugin_telegram_forwarder.core.senders.qq_file_fallback"
        path = _repo_root / "core" / "senders" / "qq_file_fallback.py"
        spec = importlib.util.spec_from_file_location(full_name, str(path))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core.senders"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        plugin_conftest._restore_mock_package_tree(previous)


def _file_component(module, *, name, file="", source_path=None):
    """构造一个 File 组件替身（conftest 的 _make_comp File 类）。"""
    comp = module.File(file=file, url="", name=name)
    if source_path is not None:
        try:
            object.__setattr__(comp, "_tgf_source_path", source_path)
        except Exception:
            comp.__dict__["_tgf_source_path"] = source_path
    return comp


class TestNormalizeApkFallbackMode:
    def test_canonical_values_pass_through(self):
        m = load_fallback_module()
        for raw in ("off", "zip", "link", "link_or_zip"):
            assert m.normalize_apk_fallback_mode(raw) == raw

    def test_chinese_and_english_aliases(self):
        m = load_fallback_module()
        assert m.normalize_apk_fallback_mode("关闭") == "off"
        assert m.normalize_apk_fallback_mode("disabled") == "off"
        assert m.normalize_apk_fallback_mode("压缩包") == "zip"
        assert m.normalize_apk_fallback_mode("archive") == "zip"
        assert m.normalize_apk_fallback_mode("直链") == "link"
        assert m.normalize_apk_fallback_mode("direct_link") == "link"
        assert m.normalize_apk_fallback_mode("直链优先，失败转压缩包") == "link_or_zip"
        assert m.normalize_apk_fallback_mode("link-first") == "link_or_zip"

    def test_empty_none_unknown_default_off(self):
        m = load_fallback_module()
        assert m.normalize_apk_fallback_mode("") == "off"
        assert m.normalize_apk_fallback_mode(None) == "off"
        assert m.normalize_apk_fallback_mode("nonsense") == "off"

    def test_case_insensitive(self):
        m = load_fallback_module()
        assert m.normalize_apk_fallback_mode("ZIP") == "zip"
        assert m.normalize_apk_fallback_mode(" Link ") == "link"


class TestResolveApkFallbackPolicy:
    def test_reads_forward_cfg_values(self):
        m = load_fallback_module()
        policy = m.resolve_apk_fallback_policy(
            {
                "apk_fallback_mode": "直链",
                "apk_direct_link_base_url": "https://x.com/dl/",
            }
        )
        assert policy.mode == "link"
        assert policy.direct_link_base_url == "https://x.com/dl/"

    def test_defaults_when_missing(self):
        m = load_fallback_module()
        policy = m.resolve_apk_fallback_policy({})
        assert policy.mode == "link_or_zip"
        assert policy.direct_link_base_url == ""

    def test_strips_base_url_whitespace(self):
        m = load_fallback_module()
        policy = m.resolve_apk_fallback_policy(
            {"apk_direct_link_base_url": "  https://x.com/dl/  "}
        )
        assert policy.direct_link_base_url == "https://x.com/dl/"


class TestBuildDirectLink:
    def test_appends_filename_to_base_with_trailing_slash(self):
        m = load_fallback_module()
        assert (
            m._build_direct_link("https://files.example.com/downloads/", "app.apk")
            == "https://files.example.com/downloads/app.apk"
        )

    def test_adds_missing_trailing_slash(self):
        m = load_fallback_module()
        assert (
            m._build_direct_link("https://files.example.com/dl", "app.apk")
            == "https://files.example.com/dl/app.apk"
        )

    def test_quotes_special_filename(self):
        m = load_fallback_module()
        url = m._build_direct_link("https://x.com/dl/", "中文 file.apk")
        assert url.endswith("%E4%B8%AD%E6%96%87%20file.apk")


class TestIsApkComponent:
    def test_apk_extension_in_name(self):
        m = load_fallback_module()
        assert m._is_apk_component(_file_component(m, name="app.apk")) is True

    def test_xapk_apkm_apks_extensions(self):
        m = load_fallback_module()
        for ext in (".xapk", ".apkm", ".apks"):
            assert m._is_apk_component(_file_component(m, name=f"app{ext}")) is True

    def test_apk_in_source_path_only(self, tmp_path):
        m = load_fallback_module()
        comp = _file_component(
            m, name="renamed.txt", source_path=str(tmp_path / "real.apk")
        )
        assert m._is_apk_component(comp) is True

    def test_non_apk_returns_false(self):
        m = load_fallback_module()
        assert m._is_apk_component(_file_component(m, name="doc.pdf")) is False


class TestIsRichMediaFailure:
    def _cls(self):
        return lambda e: "other"

    def test_rich_media_message_text(self):
        m = load_fallback_module()
        assert (
            m._is_rich_media_failure(
                Exception("rich media transfer failed"), self._cls()
            )
            is True
        )

    def test_retcode_1200_message_text(self):
        m = load_fallback_module()
        assert (
            m._is_rich_media_failure(Exception("retcode=1200 error"), self._cls())
            is True
        )

    def test_classified_retcode_1200(self):
        m = load_fallback_module()
        assert (
            m._is_rich_media_failure(Exception("oops"), lambda e: "retcode_1200")
            is True
        )

    def test_unrelated_error(self):
        m = load_fallback_module()
        assert (
            m._is_rich_media_failure(Exception("network reset"), lambda e: "timeout")
            is False
        )


class TestCreateApkZipArchive:
    def test_creates_valid_zip(self, tmp_path):
        m = load_fallback_module()
        source = tmp_path / "original.apk"
        source.write_bytes(b"fake-apk-content")
        archive_path, archive_name = m._create_apk_zip_archive(
            str(source), "original.apk", plugin_data_dir=tmp_path
        )
        assert archive_name == "original.apk.zip"
        assert Path(archive_path).is_file()
        with zipfile.ZipFile(archive_path) as zf:
            assert zf.namelist() == ["original.apk"]
            assert zf.read("original.apk") == b"fake-apk-content"

    def test_avoids_overwriting_existing_archive(self, tmp_path):
        m = load_fallback_module()
        source = tmp_path / "app.apk"
        source.write_bytes(b"v1")
        (tmp_path / "app.apk.zip").write_bytes(b"placeholder")
        archive_path, archive_name = m._create_apk_zip_archive(
            str(source), "app.apk", plugin_data_dir=tmp_path
        )
        assert archive_name == "app.apk.1.zip"
        assert Path(archive_path).is_file()


class TestTryNonApkFileFallback:
    @pytest.mark.asyncio
    async def test_direct_link_fallback_succeeds(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        comp = _file_component(m, name="doc.pdf")
        ok = await m._try_non_apk_file_fallback(
            forward_cfg={"file_direct_link_base_url": "https://x.com/dl/"},
            component=comp,
            error=Exception("rich media transfer failed"),
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
        )
        assert ok is True
        send_fn.assert_awaited_once()
        chain = send_fn.await_args.args[1]
        assert "直链" in chain.chain[0].text
        assert "doc.pdf" in chain.chain[0].text
        assert send_fn.await_args.kwargs["send_kind"] == "fallback_link"

    @pytest.mark.asyncio
    async def test_no_base_no_source_returns_false(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        comp = _file_component(m, name="doc.pdf", file="/missing/doc.pdf")
        ok = await m._try_non_apk_file_fallback(
            forward_cfg={},
            component=comp,
            error=Exception("rich media transfer failed"),
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rich_media_reject_skips_source_retry(self, tmp_path):
        """QQ 已读到文件但上传被拒（风控类）：换路径重试无意义，直接交还队列。"""
        m = load_fallback_module()
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"pdf-bytes")
        send_fn = AsyncMock()
        comp = _file_component(m, name="doc.pdf", file=str(source))
        ok = await m._try_non_apk_file_fallback(
            forward_cfg={},
            component=comp,
            error=Exception(
                'ActionFailed retcode=1200 {"errMsg": "rich media transfer failed"}'
            ),
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: "/container" + p.replace("\\", "/"),
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_identity_mapped_source_skips_retry(self, tmp_path):
        """源路径与主发送载荷相同（无映射）：原样重发必然同结果，跳过。"""
        m = load_fallback_module()
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"pdf-bytes")
        send_fn = AsyncMock()
        comp = _file_component(m, name="doc.pdf", file=str(source))
        ok = await m._try_non_apk_file_fallback(
            forward_cfg={},
            component=comp,
            error=Exception(
                "ActionFailed retcode=1200 ENOENT: no such file or directory, copyfile"
            ),
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enoent_with_mapping_retries_raw_source_path(self, tmp_path):
        """映射路径读不到且源路径拼写不同：保留原始路径救援（同机映射配错场景）。"""
        m = load_fallback_module()
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"pdf-bytes")
        send_fn = AsyncMock()
        comp = _file_component(m, name="doc.pdf", file=str(source))
        ok = await m._try_non_apk_file_fallback(
            forward_cfg={},
            component=comp,
            error=Exception(
                "ActionFailed retcode=1200 ENOENT: no such file or directory, copyfile"
            ),
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: "/container" + p.replace("\\", "/"),
        )
        assert ok is True
        send_fn.assert_awaited_once()
        assert send_fn.await_args.kwargs["send_kind"] == "fallback_file_source"
        sent_component = send_fn.await_args.args[1].chain[0]
        assert getattr(sent_component, "file", None) == str(source)


class TestHandleFileSendFailure:
    @pytest.mark.asyncio
    async def test_non_rich_media_returns_false(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        ok = await m.handle_file_send_failure(
            forward_cfg={},
            apk_policy=m.ApkFallbackPolicy(mode="off", direct_link_base_url=""),
            component=_file_component(m, name="doc.pdf"),
            error=Exception("some other error"),
            batch_data={"batch_index": 0, "nodes_data": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "other",
            plugin_data_dir=None,
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_apk_rich_media_delegates_to_direct_link(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        ok = await m.handle_file_send_failure(
            forward_cfg={"file_direct_link_base_url": "https://x.com/dl/"},
            apk_policy=m.ApkFallbackPolicy(mode="off", direct_link_base_url=""),
            component=_file_component(m, name="doc.pdf"),
            error=Exception("rich media transfer failed"),
            batch_data={"batch_index": 0, "nodes_data": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "retcode_1200",
            plugin_data_dir=None,
        )
        assert ok is True
        send_fn.assert_awaited_once()


class TestHandleApkFileSendFailure:
    @pytest.mark.asyncio
    async def test_mode_off_returns_false_even_on_rich_media(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        ok = await m.handle_apk_file_send_failure(
            policy=m.ApkFallbackPolicy(
                mode="off", direct_link_base_url="https://x.com/dl/"
            ),
            component=_file_component(m, name="app.apk"),
            error=Exception("rich media transfer failed"),
            batch_data={"batch_index": 0, "nodes_data": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "retcode_1200",
            plugin_data_dir=None,
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_apk_component_returns_false(self):
        m = load_fallback_module()
        send_fn = AsyncMock()
        ok = await m.handle_apk_file_send_failure(
            policy=m.ApkFallbackPolicy(
                mode="link", direct_link_base_url="https://x.com/dl/"
            ),
            component=_file_component(m, name="doc.pdf"),
            error=Exception("rich media transfer failed"),
            batch_data={"batch_index": 0, "nodes_data": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "retcode_1200",
            plugin_data_dir=None,
        )
        assert ok is False
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_link_mode_sends_direct_link(self, tmp_path):
        m = load_fallback_module()
        send_fn = AsyncMock()
        source = tmp_path / "app.apk"
        ok = await m.handle_apk_file_send_failure(
            policy=m.ApkFallbackPolicy(
                mode="link", direct_link_base_url="https://x.com/dl/"
            ),
            component=_file_component(m, name="app.apk", file=str(source)),
            error=Exception("rich media transfer failed"),
            batch_data={"batch_index": 0, "nodes_data": [], "local_files": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "retcode_1200",
            plugin_data_dir=None,
        )
        assert ok is True
        send_fn.assert_awaited_once()
        assert send_fn.await_args.kwargs["send_kind"] == "fallback_link"

    @pytest.mark.asyncio
    async def test_link_mode_reraises_when_direct_link_fails(self, tmp_path):
        m = load_fallback_module()
        send_fn = AsyncMock(side_effect=RuntimeError("boom"))
        source = tmp_path / "app.apk"
        with pytest.raises(RuntimeError):
            await m.handle_apk_file_send_failure(
                policy=m.ApkFallbackPolicy(
                    mode="link", direct_link_base_url="https://x.com/dl/"
                ),
                component=_file_component(m, name="app.apk", file=str(source)),
                error=Exception("rich media transfer failed"),
                batch_data={"batch_index": 0, "nodes_data": []},
                unified_msg_origin="umo",
                target_session="g1",
                send_message_fn=send_fn,
                map_path=lambda p: p,
                classify_send_error=lambda e: "retcode_1200",
                plugin_data_dir=None,
            )

    @pytest.mark.asyncio
    async def test_zip_mode_creates_archive(self, tmp_path):
        m = load_fallback_module()
        source = tmp_path / "app.apk"
        source.write_bytes(b"apk-bytes")
        send_fn = AsyncMock()
        ok = await m.handle_apk_file_send_failure(
            policy=m.ApkFallbackPolicy(mode="zip", direct_link_base_url=""),
            component=_file_component(m, name="app.apk", file=str(source)),
            error=Exception("rich media transfer failed"),
            batch_data={"batch_index": 0, "nodes_data": [], "local_files": []},
            unified_msg_origin="umo",
            target_session="g1",
            send_message_fn=send_fn,
            map_path=lambda p: p,
            classify_send_error=lambda e: "retcode_1200",
            plugin_data_dir=tmp_path,
        )
        assert ok is True
        send_fn.assert_awaited_once()
        assert send_fn.await_args.kwargs["send_kind"] == "fallback_zip"
