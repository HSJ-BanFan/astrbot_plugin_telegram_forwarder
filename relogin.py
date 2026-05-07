import asyncio
import os
from urllib.parse import urlparse

import socks
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

# 定义路径
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# 尝试定位标准 AstrBot 数据目录 (../../plugin_data/astrbot_plugin_telegram_forwarder)
# 假设结构: data/plugins/this_plugin -> data/plugin_data/this_plugin
EXPECTED_DATA_DIR = os.path.abspath(
    os.path.join(
        PLUGIN_DIR, "..", "..", "plugin_data", "astrbot_plugin_telegram_forwarder"
    )
)

if os.path.exists(EXPECTED_DATA_DIR):
    DATA_DIR = EXPECTED_DATA_DIR
    print(f"已定位数据目录: {DATA_DIR}")
else:
    DATA_DIR = PLUGIN_DIR
    print(f"未找到标准数据目录，使用当前目录: {DATA_DIR}")
    print(
        "警告: 这可能导致主程序无法读取生成的 Session 文件。请确保插件已正确安装运行过一次。"
    )

SESSION_FILE = os.path.join(DATA_DIR, "user_session")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
if not os.path.exists(CONFIG_FILE):
    # Fallback to looking in plugin dir if config not in data dir yet
    CONFIG_FILE = os.path.join(PLUGIN_DIR, "config.json")

# 由于 AstrBot 配置可能由框架管理，这里我们尝试从 data/config/... 读取，或者直接手动填入
# 为了方便，请您直接在此处填入您的 API ID 和 Hash，或者我们尝试交互式输入
print("=== Telegram Forwarder 重新登录助手 ===")

api_id_raw = input("请输入 API ID: ").strip()
try:
    api_id = int(api_id_raw)
except ValueError:
    raise SystemExit("API ID 必须是整数")
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


async def _async_input(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def main():
    print(f"正在连接... (Session路径: {SESSION_FILE})")
    client = None
    try:
        client = TelegramClient(SESSION_FILE, api_id, api_hash, proxy=proxy_setting)

        await client.connect()

        if not await client.is_user_authorized():
            print("未授权，开始登录流程...")
            phone = await _async_input("请输入手机号 (带国际区号, 如 +86138...): ")
            await client.send_code_request(phone)

            code = await _async_input("请输入您收到的验证码: ")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                pw = await _async_input("请输入两步验证密码: ")
                await client.sign_in(password=pw)
            except Exception as e:
                print(f"登录失败: {e}")
                return

        print("登录成功！")
        me = await client.get_me()
        print(f"当前用户: {me.first_name} (@{me.username})")
        print("Session 文件已更新。现在您可以重启 AstrBot 了。")
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                print(f"断开 Telegram 客户端连接失败（已忽略）: {e}")


if __name__ == "__main__":
    asyncio.run(main())
