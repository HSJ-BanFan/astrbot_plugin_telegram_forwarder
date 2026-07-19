"""qq_media 媒体分发与合并判定单元测试。"""

import importlib.util
from pathlib import Path

import conftest as plugin_conftest

_repo_root = Path(__file__).resolve().parents[1]


def load_media_module():
    previous = plugin_conftest._register_mock_package_tree()
    try:
        full_name = "astrbot_plugin_telegram_forwarder.core.senders.qq_media"
        path = _repo_root / "core" / "senders" / "qq_media.py"
        spec = importlib.util.spec_from_file_location(full_name, str(path))
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder.core.senders"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod
    finally:
        plugin_conftest._restore_mock_package_tree(previous)


class TestDispatchMediaFileVideoLimit:
    def test_small_video_stays_video(self, tmp_path):
        m = load_media_module()
        video_path = tmp_path / "clip.mp4"
        video_path.write_bytes(b"0" * 1024)
        components = m.dispatch_media_file(str(video_path), map_path=lambda p: p)
        assert len(components) == 1
        assert isinstance(components[0], m.Video)

    def test_oversized_video_becomes_file(self, tmp_path):
        m = load_media_module()
        video_path = tmp_path / "big.mp4"
        # 用稀疏写避免真写 100MB+：用 seek 制造 >100MB 文件大小
        with video_path.open("wb") as f:
            f.seek(m.QQ_VIDEO_AS_VIDEO_MAX_BYTES)
            f.write(b"x")
        components = m.dispatch_media_file(str(video_path), map_path=lambda p: p)
        assert len(components) == 1
        assert isinstance(components[0], m.File)
        assert getattr(components[0], "name", None) == "big.mp4"
        assert getattr(components[0], "_tgf_source_path", None) == str(video_path)

    def test_exact_limit_stays_video(self, tmp_path):
        m = load_media_module()
        video_path = tmp_path / "edge.mp4"
        with video_path.open("wb") as f:
            f.seek(m.QQ_VIDEO_AS_VIDEO_MAX_BYTES - 1)
            f.write(b"x")
        components = m.dispatch_media_file(str(video_path), map_path=lambda p: p)
        assert len(components) == 1
        assert isinstance(components[0], m.Video)


class TestShouldMergeBatchNodes:
    def test_images_can_merge(self):
        m = load_media_module()
        batch = {
            "batch_index": 0,
            "nodes_data": [[m.Image.fromFileSystem("a.jpg")], [m.Image.fromFileSystem("b.jpg")]],
            "local_files": [],
            "contains_audio": False,
        }
        assert m.should_merge_batch_nodes(batch) is True

    def test_video_blocks_merge(self):
        m = load_media_module()
        batch = {
            "batch_index": 0,
            "nodes_data": [[m.Video.fromFileSystem("a.mp4")], [m.Image.fromFileSystem("b.jpg")]],
            "local_files": [],
            "contains_audio": False,
        }
        assert m.should_merge_batch_nodes(batch) is False
