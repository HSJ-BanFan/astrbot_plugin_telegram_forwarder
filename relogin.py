import asyncio
import os
import json
from telethon import TelegramClient
import socks
from urllib.parse import urlparse

# 定义路径
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(PLUGIN_DIR, "user_session")
CONFIG_FILE = os.path.join(
    PLUGIN_DIR, "config.json"
)  # 假设配置文件在这里，如果不是请指出

# 由于 AstrBot 配置可能由框架管理，这里我们尝试从 data/config/... 读取，或者直接手动填入
# 为了方便，请您直接在此处填入您的 API ID 和 Hash，或者我们尝试交互式输入
print("=== Telegram Forwarder 重新登录助手 ===")

api_id = input("请输入 API ID: ").strip()
api_hash = input("请输入 API Hash: ").strip()
proxy_url = input(
    "请输入代理地址 (可选, 如 http://127.0.0.1:10801, 直接回车跳过): "
).strip()

proxy_setting = None
if proxy_url:
    try:
        parsed = urlparse(proxy_url)
        proxy_type = socks.HTTP if parsed.scheme.startswith("http") else socks.SOCKS5
        proxy_setting = (proxy_type, parsed.hostname, parsed.port)
        print(f"使用代理: {proxy_setting}")
    except Exception as e:
        print(f"代理设置错误: {e}")


async def main():
    print(f"正在连接... (Session路径: {SESSION_FILE})")
    client = TelegramClient(SESSION_FILE, api_id, api_hash, proxy=proxy_setting)

    await client.connect()

    if not await client.is_user_authorized():
        print("未授权，开始登录流程...")
        phone = input("请输入手机号 (带国际区号, 如 +86138...): ").strip()
        await client.send_code_request(phone)

        code = input("请输入您收到的验证码: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            if "password" in str(e).lower():
                pw = input("请输入两步验证密码: ").strip()
                await client.sign_in(password=pw)
            else:
                print(f"登录失败: {e}")
                return

    print("登录成功！")
    me = await client.get_me()
    print(f"当前用户: {me.first_name} (@{me.username})")
    print("Session 文件已更新。现在您可以重启 AstrBot 了。")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
