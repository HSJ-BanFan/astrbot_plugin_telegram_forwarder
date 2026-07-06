import asyncio
import importlib.util
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

PLUGIN_NAME = "astrbot_plugin_telegram_forwarder"


def load_client_module(
    telethon_version: str = "1.42.0", client_factory: MagicMock | None = None
):
    root = Path(__file__).resolve().parents[1]
    module_path = root / "core" / "client.py"
    module_name = "astrbot_plugin_telegram_forwarder.core.client"

    stubbed_modules = {
        "socks": SimpleNamespace(HTTP=1, SOCKS5=2),
        "telethon": SimpleNamespace(
            TelegramClient=client_factory or MagicMock(),
            __version__=telethon_version,
        ),
        "astrbot": MagicMock(),
        "astrbot.api": SimpleNamespace(
            logger=MagicMock(),
            AstrBotConfig=dict,
        ),
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod


def make_five_column_session_db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sessions (
            dc_id integer primary key,
            server_address text,
            port integer,
            auth_key blob,
            takeout_id integer
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sessions
        (dc_id, server_address, port, auth_key, takeout_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (5, "149.154.167.51", 443, b"auth-key", 9),
    )
    conn.commit()
    conn.close()


def make_current_session_db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sessions (
            dc_id integer primary key,
            server_address text,
            port integer,
            auth_key blob,
            takeout_id integer,
            tmp_auth_key blob
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sessions
        (dc_id, server_address, port, auth_key, takeout_id, tmp_auth_key)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (5, "149.154.167.51", 443, b"auth-key", 9, b"tmp-key"),
    )
    conn.commit()
    conn.close()


def make_test_dir() -> Path:
    root = Path(__file__).resolve().parents[1] / ".pytest_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"session-schema-{uuid.uuid4().hex}" / PLUGIN_NAME
    path.mkdir(parents=True)
    return path


def load_main_module(data_dir: Path):
    root = Path(__file__).resolve().parents[1]
    module_path = root / "main.py"
    module_name = "astrbot_plugin_telegram_forwarder.main"

    def command_group(*args, **kwargs):
        def decorate(func):
            func.command = lambda *a, **kw: lambda handler: handler
            return func

        return decorate

    filter_stub = SimpleNamespace(
        PermissionType=SimpleNamespace(ADMIN="admin"),
        command_group=command_group,
        permission_type=lambda *args, **kwargs: lambda func: func,
    )
    star_base = type("Star", (), {"__init__": lambda self, *args, **kwargs: None})
    star_stub = SimpleNamespace(Star=star_base, Context=object)
    path_utils_stub = SimpleNamespace(
        get_astrbot_plugin_data_path=lambda: data_dir.parent
    )
    telegram_wrapper = MagicMock()

    stubbed_modules = {
        "apscheduler": MagicMock(),
        "apscheduler.schedulers": MagicMock(),
        "apscheduler.schedulers.asyncio": SimpleNamespace(AsyncIOScheduler=MagicMock()),
        "astrbot": MagicMock(),
        "astrbot.api": SimpleNamespace(
            AstrBotConfig=dict, logger=MagicMock(), star=star_stub
        ),
        "astrbot.api.event": SimpleNamespace(
            AstrMessageEvent=object, filter=filter_stub
        ),
        "astrbot.api.web": SimpleNamespace(
            error_response=lambda message, status_code=400, data=None, headers=None: {
                "status": "error",
                "message": message,
                "data": data or {},
                "status_code": status_code,
            },
            json_response=lambda data=None, status_code=200, headers=None: {
                "status_code": status_code,
                "data": data or {},
            },
            request=MagicMock(),
        ),
        "astrbot.core": MagicMock(),
        "astrbot.core.utils": SimpleNamespace(path_utils=path_utils_stub),
        "astrbot.core.utils.path_utils": path_utils_stub,
        "astrbot_plugin_telegram_forwarder": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.common": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.common.storage": SimpleNamespace(
            Storage=MagicMock()
        ),
        "astrbot_plugin_telegram_forwarder.core": MagicMock(__path__=[]),
        "astrbot_plugin_telegram_forwarder.core.client": SimpleNamespace(
            TelegramClientWrapper=telegram_wrapper
        ),
        "astrbot_plugin_telegram_forwarder.core.commands": SimpleNamespace(
            PluginCommands=MagicMock()
        ),
        "astrbot_plugin_telegram_forwarder.core.forwarder": SimpleNamespace(
            Forwarder=MagicMock()
        ),
    }

    with patch.dict(sys.modules, stubbed_modules):
        sys.modules.pop(module_name, None)
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "astrbot_plugin_telegram_forwarder"
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        return mod


def test_load_client_module_restores_sys_modules_after_stubbing():
    sentinel_socks = object()
    sentinel_telethon = object()
    sentinel_astrbot = object()
    sentinel_api = object()

    with patch.dict(
        sys.modules,
        {
            "socks": sentinel_socks,
            "telethon": sentinel_telethon,
            "astrbot": sentinel_astrbot,
            "astrbot.api": sentinel_api,
        },
    ):
        load_client_module()

        assert sys.modules["socks"] is sentinel_socks
        assert sys.modules["telethon"] is sentinel_telethon
        assert sys.modules["astrbot"] is sentinel_astrbot
        assert sys.modules["astrbot.api"] is sentinel_api


def test_main_rejects_uploaded_session_path_outside_plugin_data_dir():
    tmp_dir = make_test_dir()
    outside_file = tmp_dir.parent / f"outside-{tmp_dir.name}.session"
    outside_file.write_text("external session", encoding="utf-8")
    try:
        main_module = load_main_module(tmp_dir)

        main_module.Main(MagicMock(), {"telegram_session": [f"../{outside_file.name}"]})

        assert not (tmp_dir / "user_session.session").exists()
        main_module.TelegramClientWrapper.clear_cache.assert_not_called()
    finally:
        outside_file.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_main_accepts_uploaded_session_path_inside_plugin_data_dir():
    tmp_dir = make_test_dir()
    uploaded_file = tmp_dir / "uploaded.session"
    uploaded_file.write_text("session", encoding="utf-8")
    try:
        main_module = load_main_module(tmp_dir)

        main_module.Main(MagicMock(), {"telegram_session": [uploaded_file.name]})

        assert (tmp_dir / "user_session.session").read_text(
            encoding="utf-8"
        ) == "session"
        main_module.TelegramClientWrapper.clear_cache.assert_called_with(
            str(tmp_dir / "user_session")
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_skip_migration_for_five_column_session_schema():
    client_module = load_client_module()
    wrapper = client_module.TelegramClientWrapper

    tmp_dir = make_test_dir()
    try:
        session_path = tmp_dir / "user_session"
        session_file = tmp_dir / "user_session.session"
        make_five_column_session_db(session_file)

        wrapper._ensure_compatible_session_schema(str(session_path))

        conn = sqlite3.connect(session_file)
        cols = conn.execute("PRAGMA table_info(sessions)").fetchall()
        row = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()

        assert [col[1] for col in cols] == [
            "dc_id",
            "server_address",
            "port",
            "auth_key",
            "takeout_id",
        ]
        assert row == (5, "149.154.167.51", 443, b"auth-key", 9)
        assert not (tmp_dir / "user_session.session.bak").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_strip_tmp_auth_key_session_schema_when_needed():
    client_module = load_client_module()
    wrapper = client_module.TelegramClientWrapper

    tmp_dir = make_test_dir()
    try:
        session_path = tmp_dir / "user_session"
        session_file = tmp_dir / "user_session.session"
        make_current_session_db(session_file)

        wrapper._ensure_compatible_session_schema(str(session_path))

        conn = sqlite3.connect(session_file)
        cols = conn.execute("PRAGMA table_info(sessions)").fetchall()
        row = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()

        assert [col[1] for col in cols] == [
            "dc_id",
            "server_address",
            "port",
            "auth_key",
            "takeout_id",
        ]
        assert row == (5, "149.154.167.51", 443, b"auth-key", 9)
        assert (tmp_dir / "user_session.session.bak").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_init_client_does_not_block_older_telethon_versions_by_default():
    telegram_client = MagicMock(return_value=MagicMock())
    client_module = load_client_module(
        telethon_version="1.42.0", client_factory=telegram_client
    )

    tmp_dir = make_test_dir()
    try:
        wrapper = client_module.TelegramClientWrapper(
            {"api_id": 123, "api_hash": "hash"}, tmp_dir
        )

        assert wrapper.client is not None
        telegram_client.assert_called_once()
    finally:
        client_module.get_client_cache().clear()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_init_client_blocks_unsupported_telethon_versions_with_guidance():
    telegram_client = MagicMock(return_value=MagicMock())
    client_module = load_client_module(
        telethon_version="1.43.2", client_factory=telegram_client
    )

    tmp_dir = make_test_dir()
    try:
        wrapper = client_module.TelegramClientWrapper(
            {"api_id": 123, "api_hash": "hash"}, tmp_dir
        )

        assert wrapper.client is None
        telegram_client.assert_not_called()
        assert str(tmp_dir / "user_session") not in client_module.get_client_cache()
        error_messages = " ".join(
            str(call.args[0]) for call in client_module.logger.error.call_args_list
        )
        assert "Telethon 版本 1.43.2" in error_messages
        assert ">=1.42.0,<1.43.0" in error_messages
    finally:
        client_module.get_client_cache().clear()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_init_client_reports_expected_six_schema_error_as_dependency_issue():
    telegram_client = MagicMock(
        side_effect=ValueError("not enough values to unpack (expected 6, got 5)")
    )
    client_module = load_client_module(
        telethon_version="", client_factory=telegram_client
    )

    tmp_dir = make_test_dir()
    try:
        wrapper = client_module.TelegramClientWrapper(
            {"api_id": 123, "api_hash": "hash"}, tmp_dir
        )

        assert wrapper.client is None
        telegram_client.assert_called_once()
        assert str(tmp_dir / "user_session") not in client_module.get_client_cache()
        error_messages = " ".join(
            str(call.args[0]) for call in client_module.logger.error.call_args_list
        )
        assert "Telethon 版本 unknown" in error_messages
        assert ">=1.42.0,<1.43.0" in error_messages
    finally:
        client_module.get_client_cache().clear()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_init_client_repairs_schema_error_and_retries_constructor():
    client = MagicMock()
    telegram_client = MagicMock(
        side_effect=[
            ValueError("too many values to unpack (expected 5)"),
            client,
        ]
    )
    client_module = load_client_module(client_factory=telegram_client)

    tmp_dir = make_test_dir()
    try:
        make_current_session_db(tmp_dir / "user_session.session")

        wrapper = client_module.TelegramClientWrapper(
            {"api_id": 123, "api_hash": "hash"}, tmp_dir
        )

        assert wrapper.client is client
        assert telegram_client.call_count == 2
        assert (tmp_dir / "user_session.session.bak").exists()
        assert str(tmp_dir / "user_session") in client_module.get_client_cache()
    finally:
        client_module.get_client_cache().clear()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_restore_backup_when_migration_write_fails():
    client_module = load_client_module()
    wrapper = client_module.TelegramClientWrapper

    tmp_dir = make_test_dir()
    try:
        session_path = tmp_dir / "user_session"
        session_file = tmp_dir / "user_session.session"
        make_current_session_db(session_file)

        real_connect = client_module.sqlite3.connect
        connect_calls = 0

        class FailingMigrationConnection:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args, **kwargs):
                if "ALTER TABLE sessions RENAME" in sql:
                    raise sqlite3.OperationalError("simulated migration failure")
                return self._conn.execute(sql, *args, **kwargs)

            def commit(self):
                return self._conn.commit()

            def close(self):
                return self._conn.close()

        def connect_with_failing_migration(*args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            conn = real_connect(*args, **kwargs)
            if connect_calls == 2:
                return FailingMigrationConnection(conn)
            return conn

        with patch.object(
            client_module.sqlite3, "connect", side_effect=connect_with_failing_migration
        ):
            try:
                wrapper._ensure_compatible_session_schema(str(session_path))
            except sqlite3.OperationalError:
                pass

        conn = sqlite3.connect(session_file)
        cols = conn.execute("PRAGMA table_info(sessions)").fetchall()
        row = conn.execute("SELECT * FROM sessions").fetchone()
        legacy_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions_legacy'"
        ).fetchone()
        conn.close()

        assert [col[1] for col in cols] == [
            "dc_id",
            "server_address",
            "port",
            "auth_key",
            "takeout_id",
            "tmp_auth_key",
        ]
        assert row == (5, "149.154.167.51", 443, b"auth-key", 9, b"tmp-key")
        assert legacy_exists is None
        assert (tmp_dir / "user_session.session.bak").exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_main_registers_dashboard_page_web_apis():
    tmp_dir = make_test_dir()
    try:
        main_module = load_main_module(tmp_dir)
        context = MagicMock()

        main_module.Main(context, {})

        registered_routes = [
            call.args[0] for call in context.register_web_api.call_args_list
        ]
        assert f"/{PLUGIN_NAME}/status" in registered_routes
        assert f"/{PLUGIN_NAME}/config" in registered_routes
        assert f"/{PLUGIN_NAME}/login/start" in registered_routes
        assert f"/{PLUGIN_NAME}/runtime/check" in registered_routes
        assert all(route.startswith(f"/{PLUGIN_NAME}/") for route in registered_routes)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_refreshes_stale_backup_before_failed_migration_rollback():
    client_module = load_client_module()
    wrapper = client_module.TelegramClientWrapper

    tmp_dir = make_test_dir()
    try:
        session_path = tmp_dir / "user_session"
        session_file = tmp_dir / "user_session.session"
        backup_file = tmp_dir / "user_session.session.bak"
        make_current_session_db(session_file)
        make_current_session_db(backup_file)

        conn = sqlite3.connect(session_file)
        conn.execute(
            """
            UPDATE sessions
            SET auth_key = ?, takeout_id = ?, tmp_auth_key = ?
            """,
            (b"current-auth", 42, b"current-tmp"),
        )
        conn.commit()
        conn.close()

        conn = sqlite3.connect(backup_file)
        conn.execute(
            """
            UPDATE sessions
            SET auth_key = ?, takeout_id = ?, tmp_auth_key = ?
            """,
            (b"stale-auth", 7, b"stale-tmp"),
        )
        conn.commit()
        conn.close()

        real_connect = client_module.sqlite3.connect
        connect_calls = 0

        class FailingMigrationConnection:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args, **kwargs):
                if "ALTER TABLE sessions RENAME" in sql:
                    raise sqlite3.OperationalError("simulated migration failure")
                return self._conn.execute(sql, *args, **kwargs)

            def commit(self):
                return self._conn.commit()

            def close(self):
                return self._conn.close()

        def connect_with_failing_migration(*args, **kwargs):
            nonlocal connect_calls
            connect_calls += 1
            conn = real_connect(*args, **kwargs)
            if connect_calls == 2:
                return FailingMigrationConnection(conn)
            return conn

        with patch.object(
            client_module.sqlite3, "connect", side_effect=connect_with_failing_migration
        ):
            try:
                wrapper._ensure_compatible_session_schema(str(session_path))
            except sqlite3.OperationalError:
                pass

        conn = sqlite3.connect(session_file)
        cols = conn.execute("PRAGMA table_info(sessions)").fetchall()
        row = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()

        assert [col[1] for col in cols] == [
            "dc_id",
            "server_address",
            "port",
            "auth_key",
            "takeout_id",
            "tmp_auth_key",
        ]
        assert row == (5, "149.154.167.51", 443, b"current-auth", 42, b"current-tmp")

        rotated_backup = tmp_dir / "user_session.session.bak.1"
        assert rotated_backup.exists()
        conn = sqlite3.connect(rotated_backup)
        stale_row = conn.execute("SELECT * FROM sessions").fetchone()
        conn.close()
        assert stale_row == (5, "149.154.167.51", 443, b"stale-auth", 7, b"stale-tmp")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_clear_cache_closes_cached_session_before_removal():
    client_module = load_client_module()
    session_path = "synthetic/session/user_session"
    cached_client = MagicMock()
    cached_client.session = MagicMock()
    client_module.get_client_cache().clear()
    client_module.get_auth_cache().clear()
    client_module.get_client_cache()[session_path] = cached_client
    client_module.get_auth_cache()[session_path] = True

    client_module.TelegramClientWrapper.clear_cache(session_path)

    cached_client.session.close.assert_called_once_with()
    assert session_path not in client_module.get_client_cache()
    assert session_path not in client_module.get_auth_cache()


def test_disconnect_and_clear_cache_closes_session_even_when_client_looks_disconnected():
    client_module = load_client_module()
    session_path = "synthetic/session/user_session"
    cached_client = MagicMock()
    cached_client.is_connected.return_value = False
    cached_client.disconnect = AsyncMock()
    cached_client.session = MagicMock()
    client_module.get_client_cache().clear()
    client_module.get_auth_cache().clear()
    client_module.get_client_cache()[session_path] = cached_client

    asyncio.run(
        client_module.TelegramClientWrapper.disconnect_and_clear_cache(session_path)
    )

    cached_client.session.close.assert_called()
    assert session_path not in client_module.get_client_cache()


def test_init_client_reports_schema_migration_failure_without_caching_client():
    telegram_client = MagicMock(
        side_effect=ValueError("too many values to unpack (expected 5)")
    )
    client_module = load_client_module(client_factory=telegram_client)
    tmp_dir = make_test_dir()
    try:
        client_module.get_client_cache().clear()
        with patch.object(
            client_module.TelegramClientWrapper,
            "_ensure_compatible_session_schema",
            side_effect=sqlite3.DatabaseError("broken schema"),
        ) as repair_schema:
            wrapper = client_module.TelegramClientWrapper(
                {"api_id": 123, "api_hash": "hash"}, tmp_dir
            )

        assert wrapper.client is None
        telegram_client.assert_called_once()
        repair_schema.assert_called_once_with(str(tmp_dir / "user_session"))
        assert str(tmp_dir / "user_session") not in client_module.get_client_cache()
        error_messages = " ".join(
            str(call.args[0]) for call in client_module.logger.error.call_args_list
        )
        assert "broken schema" in error_messages
        assert "relogin.py" in error_messages
    finally:
        client_module.get_client_cache().clear()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_redact_proxy_url_hides_credentials():
    client_module = load_client_module()

    redacted = client_module.TelegramClientWrapper._redact_proxy_url(
        "socks5://user:secret@example.com:1080"
    )

    assert redacted == "socks5://***@example.com:1080"
    assert "user" not in redacted
    assert "secret" not in redacted


def test_redact_proxy_url_preserves_url_without_credentials():
    client_module = load_client_module()

    redacted = client_module.TelegramClientWrapper._redact_proxy_url(
        "socks5://example.com:1080"
    )

    assert redacted == "socks5://example.com:1080"


def test_parse_proxy_url_includes_socks5_credentials():
    client_module = load_client_module()

    proxy = client_module.TelegramClientWrapper._parse_proxy_url(
        "socks5://admin:wowull@example.com:12311"
    )

    assert proxy == (2, "example.com", 12311, True, "admin", "wowull")


def test_parse_proxy_url_decodes_credentials():
    client_module = load_client_module()

    proxy = client_module.TelegramClientWrapper._parse_proxy_url(
        "socks5://adm%40in:wo%3Awull@example.com:12311"
    )

    assert proxy == (2, "example.com", 12311, True, "adm@in", "wo:wull")


def test_parse_proxy_url_without_credentials_keeps_short_tuple():
    client_module = load_client_module()

    proxy = client_module.TelegramClientWrapper._parse_proxy_url(
        "socks5://example.com:12311"
    )

    assert proxy == (2, "example.com", 12311)


def test_ensure_connected_rebuilds_once_on_wrong_session_id_then_returns_false_if_still_disconnected():
    client_module = load_client_module()
    wrapper = object.__new__(client_module.TelegramClientWrapper)
    wrapper.config = {"forward_config": {"wrong_session_rebuild_enabled": True}}
    wrapper.plugin_data_dir = "synthetic/session"

    old_client = MagicMock()
    old_client.is_connected.return_value = False
    old_client.connect = AsyncMock(side_effect=RuntimeError("wrong session ID"))

    new_client = MagicMock()
    new_client.is_connected.return_value = False
    new_client.connect = AsyncMock(return_value=None)

    wrapper.client = old_client
    wrapper._session_path = MagicMock(return_value="synthetic/session/user_session")
    wrapper._init_client = MagicMock(
        side_effect=lambda: setattr(wrapper, "client", new_client)
    )

    with patch.object(
        client_module.TelegramClientWrapper,
        "disconnect_and_clear_cache",
        new=AsyncMock(),
    ) as disconnect_and_clear_cache:
        connected = asyncio.run(wrapper.ensure_connected())

    assert connected is False
    old_client.connect.assert_awaited_once_with()
    disconnect_and_clear_cache.assert_awaited_once_with(
        "synthetic/session/user_session"
    )
    wrapper._init_client.assert_called_once_with()
    new_client.connect.assert_awaited_once_with()


def test_ensure_connected_reraises_wrong_session_error_when_rebuild_disabled():
    client_module = load_client_module()
    wrapper = object.__new__(client_module.TelegramClientWrapper)
    wrapper.config = {"forward_config": {"wrong_session_rebuild_enabled": False}}
    wrapper.plugin_data_dir = "synthetic/session"

    client = MagicMock()
    client.is_connected.return_value = False
    client.connect = AsyncMock(side_effect=RuntimeError("Wrong Session Id"))
    wrapper.client = client
    wrapper._session_path = MagicMock(return_value="synthetic/session/user_session")
    wrapper._init_client = MagicMock()

    with patch.object(
        client_module.TelegramClientWrapper,
        "disconnect_and_clear_cache",
        new=AsyncMock(),
    ) as disconnect_and_clear_cache:
        try:
            asyncio.run(wrapper.ensure_connected())
            assert False, "expected wrong session error to be re-raised"
        except RuntimeError as exc:
            assert "Wrong Session Id" in str(exc)

    disconnect_and_clear_cache.assert_not_awaited()
    wrapper._init_client.assert_not_called()


def test_ensure_connected_returns_false_when_second_connect_fails_after_rebuild():
    client_module = load_client_module()
    wrapper = object.__new__(client_module.TelegramClientWrapper)
    wrapper.config = {"forward_config": {"wrong_session_rebuild_enabled": True}}
    wrapper.plugin_data_dir = "synthetic/session"

    old_client = MagicMock()
    old_client.is_connected.return_value = False
    old_client.connect = AsyncMock(side_effect=RuntimeError("wrong session ID"))

    new_client = MagicMock()
    new_client.is_connected.return_value = False
    new_client.connect = AsyncMock(side_effect=RuntimeError("network still broken"))

    wrapper.client = old_client
    wrapper._session_path = MagicMock(return_value="synthetic/session/user_session")
    wrapper._init_client = MagicMock(
        side_effect=lambda: setattr(wrapper, "client", new_client)
    )

    with patch.object(
        client_module.TelegramClientWrapper,
        "disconnect_and_clear_cache",
        new=AsyncMock(),
    ) as disconnect_and_clear_cache:
        connected = asyncio.run(wrapper.ensure_connected())

    assert connected is False
    disconnect_and_clear_cache.assert_awaited_once_with(
        "synthetic/session/user_session"
    )
    wrapper._init_client.assert_called_once_with()
    new_client.connect.assert_awaited_once_with()


def test_start_routes_connection_through_ensure_connected():
    client_module = load_client_module()
    wrapper = object.__new__(client_module.TelegramClientWrapper)
    wrapper.config = {}
    wrapper.plugin_data_dir = "synthetic/session"
    wrapper._authorized = False
    wrapper._session_path = MagicMock(return_value="synthetic/session/user_session")
    wrapper.ensure_connected = AsyncMock(return_value=True)

    client = MagicMock()
    client.is_connected.return_value = False
    client.connect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=True)
    client.get_dialogs = AsyncMock()
    wrapper.client = client

    auth_cache = client_module.get_auth_cache()
    auth_cache.clear()

    asyncio.run(wrapper.start())

    wrapper.ensure_connected.assert_awaited_once_with()
    client.connect.assert_not_awaited()
    client.get_dialogs.assert_awaited_once_with(limit=None)
    assert wrapper._authorized is True
    auth_cache.clear()
