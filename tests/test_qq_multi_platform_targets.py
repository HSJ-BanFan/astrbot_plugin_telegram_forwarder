"""两个 QQ 平台实例时，手写专属目标必须落到它自己声明的那个平台。

issue #65：挂两个 QQ、两份配置互不影响，但第二份里手写的目标不搬运。

机制：`initialize_runtime()` 在插件启动时**无偏好**地选了一个平台并缓存到
`self.platform_id` / `self.bot`；之后 `send()` 把目标里的平台 ID 当
`preferred_platform_ids` 传进来，`_bootstrap_qq_runtime()` 因为
`if self.platform_id and self.bot: return` 直接早退，偏好从头到尾没被看过。
于是所有目标，包括手写成 `platform-b:GroupMessage:xxx` 的，都从 A 号 bot
发出去——A 号不在那个群，静默失败。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


def _platform(platform_id: str, bot_name: str = ""):
    bot = MagicMock()
    bot.get_login_info = AsyncMock(
        return_value={"nickname": bot_name or f"bot-{platform_id}", "user_id": 1}
    )
    platform = MagicMock()
    platform.meta = MagicMock(
        return_value=SimpleNamespace(id=platform_id, name="aiocqhttp")
    )
    platform.get_client = MagicMock(return_value=bot)
    return platform, bot


def _context(platforms):
    context = MagicMock()
    context.platform_manager = SimpleNamespace(platform_insts=list(platforms))
    return context


def test_preferred_platform_wins_over_the_one_cached_at_startup(qq_module):
    platform_a, bot_a = _platform("qq-account-a")
    platform_b, bot_b = _platform("qq-account-b")
    sender = qq_module.QQSender(
        context=_context([platform_a, platform_b]),
        config={},
        downloader=MagicMock(),
    )

    # 插件启动：没有任何目标可参考，只能按启发式选第一个。
    asyncio.run(sender.initialize_runtime())
    assert sender.platform_id == "qq-account-a"

    # 第一条真正要发的消息：目标明确写在 B 号上。
    asyncio.run(sender._bootstrap_qq_runtime(preferred_platform_ids=["qq-account-b"]))

    assert sender.platform_id == "qq-account-b"
    assert sender.bot is bot_b
    assert sender.bot is not bot_a


def test_preferred_platform_is_kept_when_it_matches_the_cached_one(qq_module):
    platform_a, bot_a = _platform("qq-account-a")
    platform_b, _ = _platform("qq-account-b")
    sender = qq_module.QQSender(
        context=_context([platform_a, platform_b]),
        config={},
        downloader=MagicMock(),
    )

    asyncio.run(sender.initialize_runtime())
    asyncio.run(sender._bootstrap_qq_runtime(preferred_platform_ids=["qq-account-a"]))

    assert sender.platform_id == "qq-account-a"
    assert sender.bot is bot_a


def test_unknown_preferred_platform_does_not_break_the_cached_one(qq_module):
    """目标里写了平台名但那个平台没加载——不能因此把已选好的平台弄丢。"""
    platform_a, bot_a = _platform("qq-account-a")
    sender = qq_module.QQSender(
        context=_context([platform_a]),
        config={},
        downloader=MagicMock(),
    )

    asyncio.run(sender.initialize_runtime())
    asyncio.run(sender._bootstrap_qq_runtime(preferred_platform_ids=["not-loaded"]))

    assert sender.platform_id == "qq-account-a"
    assert sender.bot is bot_a
