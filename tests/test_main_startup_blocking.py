"""回归测试：#53 离线/无代理启动时 `Main.initialize()` 不得阻塞 AstrBot 主服务。

启动期间 Telegram 不可达（connect 永不返回）时，initialize() 必须在短时间窗口内返回，
让 AstrBot 的 WebUI 先起来；连接在后台完成，代理恢复后应自动激活调度器。
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PLUGIN_NAME = "astrbot_plugin_telegram_forwarder"


def _load_main_with_real_client(data_dir: Path):
    """加载真实的 main.py + core/client.py，Telethon 客户端替换为可控 mock。

    返回 (main_mod, client_mod, client_factory)。main_mod.Main 构造时使用真实的
    TelegramClientWrapper，其 client 为 client_factory.return_value，测试可自由
    配置 is_connected() / connect() 以模拟离线/恢复场景。
    """
    root = Path(__file__).resolve().parents[1]
    main_name = f"{PLUGIN_NAME}.main"
    client_name = f"{PLUGIN_NAME}.core.client"
    core_name = f"{PLUGIN_NAME}.core"
    common_name = f"{PLUGIN_NAME}.common"

    client_factory = MagicMock()

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
    star_base = type("Star", (), {"__init__": lambda self, *a, **kw: None})
    star_stub = SimpleNamespace(Star=star_base, Context=object)
    path_utils_stub = SimpleNamespace(
        get_astrbot_plugin_data_path=lambda: data_dir.parent
    )

    pkg_module = type(sys)(PLUGIN_NAME)
    pkg_module.__path__ = [str(root)]
    core_module = type(sys)(core_name)
    core_module.__path__ = [str(root / "core")]
    common_module = type(sys)(common_name)
    common_module.__path__ = [str(root / "common")]

    stubbed_modules = {
        "socks": SimpleNamespace(HTTP=1, SOCKS4=3, SOCKS5=2),
        "telethon": SimpleNamespace(
            TelegramClient=client_factory, __version__="1.42.0"
        ),
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
        PLUGIN_NAME: pkg_module,
        common_name: common_module,
        f"{common_name}.storage": SimpleNamespace(Storage=MagicMock()),
        core_name: core_module,
        f"{core_name}.commands": SimpleNamespace(PluginCommands=MagicMock()),
        f"{core_name}.forwarder": SimpleNamespace(Forwarder=MagicMock()),
    }

    with patch.dict(sys.modules, stubbed_modules):
        for name in (main_name, client_name):
            sys.modules.pop(name, None)

        client_spec = importlib.util.spec_from_file_location(
            client_name, root / "core" / "client.py"
        )
        assert client_spec is not None and client_spec.loader is not None
        client_mod = importlib.util.module_from_spec(client_spec)
        client_mod.__package__ = core_name
        client_spec.loader.exec_module(client_mod)
        sys.modules[client_name] = client_mod

        main_spec = importlib.util.spec_from_file_location(main_name, root / "main.py")
        assert main_spec is not None and main_spec.loader is not None
        main_mod = importlib.util.module_from_spec(main_spec)
        main_mod.__package__ = PLUGIN_NAME
        main_spec.loader.exec_module(main_mod)
        sys.modules[main_name] = main_mod

        return main_mod, client_mod, client_factory


def _build_main(tmp_path):
    data_dir = tmp_path / "plugin_data"
    main_mod, client_mod, _client_factory = _load_main_with_real_client(data_dir)
    main = main_mod.Main(MagicMock(), {"api_id": 12345, "api_hash": "hash"})
    # 本测试只关心启动连接路径，不启动 Flask Web 管理线程。
    main._start_web_admin_server = lambda: None
    return main, client_mod


async def _teardown_main(main, client_mod):
    for name in ("_startup_connect_task", "_cache_warm_task", "_runtime_bootstrap_task"):
        task = getattr(main, name, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    client_mod.TelegramClientWrapper.clear_cache()


@pytest.mark.asyncio
async def test_initialize_returns_promptly_when_telegram_offline(tmp_path):
    """离线（connect 永不返回）时 initialize() 必须快速返回，不得阻塞 AstrBot 启动。

    RED 前：initialize() 同步 await connect()，超时即失败。
    """
    main, client_mod = _build_main(tmp_path)
    try:
        client = main.client_wrapper.client
        client.is_connected.return_value = False
        blocker = asyncio.Event()
        client.connect = AsyncMock(side_effect=blocker.wait)
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_dialogs = AsyncMock()

        # 1 秒内必须返回；否则说明启动被 Telegram 连接同步阻塞（issue #53）。
        await asyncio.wait_for(main.initialize(), timeout=1.0)
    finally:
        await _teardown_main(main, client_mod)


@pytest.mark.asyncio
async def test_offline_startup_recovers_when_connection_succeeds(tmp_path):
    """离线启动后代理恢复：后台连接完成授权并激活调度器。"""
    main, client_mod = _build_main(tmp_path)
    try:
        client = main.client_wrapper.client
        blocker = asyncio.Event()
        client.is_connected.side_effect = lambda: blocker.is_set()
        client.connect = AsyncMock(side_effect=blocker.wait)
        client.is_user_authorized = AsyncMock(return_value=True)
        client.get_dialogs = AsyncMock()
        main.activate_runtime_after_authorized = AsyncMock(return_value=True)

        await asyncio.wait_for(main.initialize(), timeout=1.0)

        # 离线时不得激活运行时。
        main.activate_runtime_after_authorized.assert_not_awaited()

        # 代理恢复：放行连接，后台启动任务应自动完成授权。
        blocker.set()
        await asyncio.wait_for(main._startup_connect_task, timeout=2.0)

        assert main.client_wrapper.is_authorized() is True
        main.activate_runtime_after_authorized.assert_awaited_once_with()
    finally:
        await _teardown_main(main, client_mod)


@pytest.mark.asyncio
async def test_ensure_connected_serializes_concurrent_connects(tmp_path):
    """多个调用方同时 ensure_connected 时，只对 client 发起一次 connect。"""
    _main_mod, client_mod, _client_factory = _load_main_with_real_client(
        tmp_path / "plugin_data"
    )
    try:
        wrapper = object.__new__(client_mod.TelegramClientWrapper)
        wrapper.config = {}
        wrapper.plugin_data_dir = "synthetic/session"
        wrapper._authorized = False
        wrapper.client = MagicMock()
        wrapper._session_path = MagicMock(return_value="synthetic/session/user_session")

        client = wrapper.client
        connected = [False]
        connect_count = 0
        first_connect_done = asyncio.Event()

        async def slow_connect():
            nonlocal connect_count
            connect_count += 1
            await first_connect_done.wait()
            connected[0] = True

        client.is_connected.side_effect = lambda: connected[0]
        client.connect = AsyncMock(side_effect=slow_connect)

        t1 = asyncio.create_task(wrapper.ensure_connected())
        await asyncio.sleep(0.02)  # 让 t1 拿到连接锁并进入 connect
        t2 = asyncio.create_task(wrapper.ensure_connected())
        await asyncio.sleep(0.02)

        first_connect_done.set()
        results = await asyncio.gather(t1, t2)

        assert results == [True, True]
        assert connect_count == 1
    finally:
        client_mod.TelegramClientWrapper.clear_cache()
