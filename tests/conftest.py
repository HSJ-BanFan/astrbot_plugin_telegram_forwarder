"""通过包路径直接加载 qq.py 的测试夹具。"""

import asyncio
import importlib.util
import inspect
import sys
from typing import Any
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "asyncio: run async test functions with asyncio when pytest-asyncio is absent",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None

    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None

    testargs = {
        arg: pyfuncitem.funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(testfunction(**testargs))
    return True


# 模拟 qq.py 使用的所有顶层导入依赖。

# Telethon 依赖。
mock_telethon_types = MagicMock()
sys.modules["telethon"] = MagicMock()
sys.modules["telethon.tl"] = MagicMock()
sys.modules["telethon.tl.types"] = mock_telethon_types

# AstrBot 模块树。
mock_logger = MagicMock()
mock_star = MagicMock()
mock_star.Context = type("Context", (), {})
mock_star.Star = type("Star", (), {"__init__": lambda self, *a, **kw: None})


def _make_comp(name):
    """创建模拟的 AstrBot 消息组件类。"""

    def _init(self, *args, **kwargs):
        if name == "MessageChain":
            initial = list(args[0]) if args else []
            self.chain = initial
            return
        if name == "Plain" and args:
            self.text = args[0]
        if name in {"Record", "Image", "Video", "File", "Node", "Nodes"} and args:
            self.value = args[0]
        for key, value in kwargs.items():
            setattr(self, key, value)

    def _from_file_system(cls2: Any, path: Any):
        return cls2(path)

    cls = type(
        name,
        (),
        {
            "__init__": _init,
            "fromFileSystem": classmethod(_from_file_system),
        },
    )
    return cls


sys.modules["astrbot"] = MagicMock()
sys.modules["astrbot.api"] = MagicMock(
    logger=mock_logger, AstrBotConfig=dict, star=mock_star
)
sys.modules["astrbot.api.star"] = mock_star
sys.modules["astrbot.api.event"] = MagicMock(MessageChain=_make_comp("MessageChain"))
sys.modules["astrbot.api.message_components"] = MagicMock(
    **{
        n: _make_comp(n)
        for n in ["Plain", "Image", "Record", "Video", "File", "Node", "Nodes"]
    }
)

# astrbot.core.utils.path_util 依赖。
sys.modules["astrbot.core"] = MagicMock()
sys.modules["astrbot.core.utils"] = MagicMock()
mock_path_util = MagicMock()
mock_path_util.path_Mapping = None
sys.modules["astrbot.core.utils.path_util"] = mock_path_util

# 模拟相对导入使用的模块。

mock_text_tools = MagicMock()
mock_text_tools.clean_telegram_text = lambda text, **kw: text
mock_text_tools.is_numeric_channel_id = lambda s: str(s).lstrip("-").isdigit()
mock_text_tools.to_telethon_entity = lambda s: (
    int(s) if str(s).lstrip("-").isdigit() else s
)

mock_downloader = MagicMock()
mock_downloader.MediaDownloader = type("MediaDownloader", (), {})

# 注册包路径，确保 qq.py 的相对导入可以解析。
_repo_root = Path(__file__).resolve().parents[1]
_pkg = "astrbot_plugin_telegram_forwarder"


def _is_plugin_module(name: str) -> bool:
    return name == _pkg or name.startswith(f"{_pkg}.")


def _register_mock_package_tree():
    previous_modules = {
        name: module for name, module in sys.modules.items() if _is_plugin_module(name)
    }

    pkg_module = type(sys)(_pkg)
    pkg_module.__path__ = [str(_repo_root)]
    sys.modules[_pkg] = pkg_module

    core_module = type(sys)(f"{_pkg}.core")
    core_module.__path__ = [str(_repo_root / "core")]
    sys.modules[f"{_pkg}.core"] = core_module

    senders_module = type(sys)(f"{_pkg}.core.senders")
    senders_module.__path__ = [str(_repo_root / "core" / "senders")]
    sys.modules[f"{_pkg}.core.senders"] = senders_module

    sys.modules[f"{_pkg}.core.senders.qq"] = MagicMock()

    common_module = type(sys)(f"{_pkg}.common")
    common_module.__path__ = [str(_repo_root / "common")]
    sys.modules[f"{_pkg}.common"] = common_module

    sys.modules[f"{_pkg}.common.text_tools"] = mock_text_tools
    sys.modules[f"{_pkg}.core.downloader"] = mock_downloader
    return previous_modules


def _restore_mock_package_tree(previous_modules):
    for name in list(sys.modules):
        if _is_plugin_module(name):
            sys.modules.pop(name, None)
    sys.modules.update(previous_modules)


# qq.py 的源文件路径。

_qq_path = str(_repo_root / "core" / "senders" / "qq.py")


def load_qq_module():
    """以隔离模块形式加载 qq.py。"""
    previous_modules = _register_mock_package_tree()
    spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_telegram_forwarder.core.senders.qq",
        _qq_path,
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "astrbot_plugin_telegram_forwarder.core.senders"

    try:
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    finally:
        _restore_mock_package_tree(previous_modules)

    return mod


@pytest.fixture
def qq_module():
    return load_qq_module()


@pytest.fixture
def sender(qq_module):
    return qq_module.QQSender(context=MagicMock(), config={}, downloader=MagicMock())
