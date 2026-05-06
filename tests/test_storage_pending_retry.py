import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


def _snapshot_modules(*names: str) -> dict[str, object | None]:
    return {name: sys.modules.get(name) for name in names}


def _restore_modules(snapshot: dict[str, object | None]) -> None:
    for name, value in snapshot.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


def load_storage_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "common" / "storage.py"
    snapshot = _snapshot_modules("astrbot", "astrbot.api")

    try:
        sys.modules["astrbot"] = MagicMock()
        sys.modules["astrbot.api"] = SimpleNamespace(logger=MagicMock())

        spec = importlib.util.spec_from_file_location(
            "astrbot_plugin_telegram_forwarder.common.storage",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        _restore_modules(snapshot)


def test_load_storage_module_restores_astrbot_modules(monkeypatch):
    sentinel = object()
    monkeypatch.setitem(sys.modules, "astrbot", sentinel)
    monkeypatch.setitem(sys.modules, "astrbot.api", sentinel)

    load_storage_module()

    assert sys.modules["astrbot"] is sentinel
    assert sys.modules["astrbot.api"] is sentinel


def make_test_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"storage-retry-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def test_add_batch_initializes_retry_fields():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    try:
        storage = storage_module.Storage(str(tmp_dir / "data.json"))
        storage.add_batch_to_pending_queue(
            "demo",
            [
                {
                    "id": 1,
                    "time": 100.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                }
            ],
        )

        item = storage.get_all_pending()[0]
        assert item["retry_count"] == 0
        assert item["next_retry_at"] == 0
        assert item["last_error_type"] == ""
        assert item["last_error_code"] == ""
        assert item["last_attempt_at"] == 0
        assert item["last_target_session"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_get_all_pending_normalizes_legacy_items():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    try:
        data_path = tmp_dir / "data.json"
        data_path.write_text(
            json.dumps(
                {
                    "channels": {
                        "demo": {
                            "pending_queue": [
                                {
                                    "id": 2,
                                    "time": 200.0,
                                    "grouped_id": None,
                                    "is_cold_start": False,
                                    "is_monitored": True,
                                }
                            ]
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        storage = storage_module.Storage(str(data_path))
        item = storage.get_all_pending()[0]
        assert item["retry_count"] == 0
        assert item["next_retry_at"] == 0
        assert item["last_error_type"] == ""
        assert item["last_error_code"] == ""
        assert item["last_attempt_at"] == 0
        assert item["last_target_session"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_mark_pending_retry_updates_targeted_items_only():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    try:
        storage = storage_module.Storage(str(tmp_dir / "data.json"))
        storage.add_batch_to_pending_queue(
            "demo",
            [
                {
                    "id": 11,
                    "time": 100.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                },
                {
                    "id": 12,
                    "time": 101.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                },
            ],
        )

        storage.mark_pending_retry(
            "demo",
            [11],
            error_type="timeout",
            target_session="test:GroupMessage:1",
            base_delay=30,
            max_delay=300,
            attempted_at=1000.0,
        )

        items = {item["id"]: item for item in storage.get_all_pending()}
        assert items[11]["retry_count"] == 1
        assert items[11]["next_retry_at"] == 1030.0
        assert items[11]["last_error_type"] == "timeout"
        assert items[11]["last_error_code"] == ""
        assert items[11]["last_attempt_at"] == 1000.0
        assert items[11]["last_target_session"] == "test:GroupMessage:1"
        assert items[12]["retry_count"] == 0
        assert items[12]["next_retry_at"] == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_clear_pending_retry_resets_targeted_items_only():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    try:
        storage = storage_module.Storage(str(tmp_dir / "data.json"))
        storage.add_batch_to_pending_queue(
            "demo",
            [
                {
                    "id": 21,
                    "time": 100.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                    "retry_count": 2,
                    "next_retry_at": 1060.0,
                    "last_error_type": "timeout",
                    "last_error_code": "",
                    "last_attempt_at": 1000.0,
                    "last_target_session": "test:GroupMessage:2",
                },
                {
                    "id": 22,
                    "time": 101.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                    "retry_count": 1,
                    "next_retry_at": 1030.0,
                    "last_error_type": "timeout",
                    "last_error_code": "",
                    "last_attempt_at": 1000.0,
                    "last_target_session": "test:GroupMessage:3",
                },
            ],
        )

        storage.clear_pending_retry("demo", [21])

        items = {item["id"]: item for item in storage.get_all_pending()}
        assert items[21]["retry_count"] == 0
        assert items[21]["next_retry_at"] == 0
        assert items[21]["last_error_type"] == ""
        assert items[21]["last_error_code"] == ""
        assert items[21]["last_attempt_at"] == 0
        assert items[21]["last_target_session"] == ""
        assert items[22]["retry_count"] == 1
        assert items[22]["next_retry_at"] == 1030.0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_mark_pending_tg_forwarded_persists_target_per_item():
    storage_module = load_storage_module()
    tmp_dir = make_test_dir()
    try:
        data_path = tmp_dir / "data.json"
        storage = storage_module.Storage(str(data_path))
        storage.add_batch_to_pending_queue(
            "demo",
            [
                {
                    "id": 31,
                    "time": 100.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                },
                {
                    "id": 32,
                    "time": 101.0,
                    "grouped_id": None,
                    "is_cold_start": False,
                    "is_monitored": False,
                },
            ],
        )

        storage.mark_pending_tg_forwarded("demo", [31], "tg-target")
        reloaded = storage_module.Storage(str(data_path))

        items = {item["id"]: item for item in reloaded.get_all_pending()}
        assert items[31]["last_tg_target"] == "tg-target"
        assert items[32]["last_tg_target"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
