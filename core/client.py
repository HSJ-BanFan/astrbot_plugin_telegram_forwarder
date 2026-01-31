from telethon import TelegramClient
import socks
from astrbot.api import logger, AstrBotConfig
import asyncio
import os

class TelegramClientWrapper:
    def __init__(self, config: AstrBotConfig, plugin_data_dir: str):
        self.config = config
        self.plugin_data_dir = plugin_data_dir
        self.client = None
        self._init_client()

    def _init_client(self):
        api_id = self.config.get("api_id")
        api_hash = self.config.get("api_hash")
         
        if api_id and api_hash:
            session_path = os.path.join(self.plugin_data_dir, "user_session")
            
            # Proxy settings
            proxy_url = self.config.get("proxy", "")
            proxy_setting = None
            if proxy_url:
                try:
                    # Very basic parse: http://ip:port
                    if "://" in proxy_url:
                         scheme, rest = proxy_url.split("://")
                         host, port = rest.split(":")
                         proxy_type = socks.HTTP if "http" in scheme else socks.SOCKS5
                         proxy_setting = (proxy_type, host, int(port))
                         logger.info(f"Using proxy: {proxy_setting}")
                except Exception as e:
                    logger.error(f"Invalid proxy format: {e}. Expected http://ip:port")

            self.client = TelegramClient(session_path, api_id, api_hash, proxy=proxy_setting)
        else:
            logger.warning("Telegram Forwarder: api_id/api_hash missing. Please configure them.")

    async def start(self):
        if not self.client: return
        
        try:
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                logger.warning("Telegram Forwarder: Client NOT authorized.")
                phone = self.config.get("phone")
                if phone:
                    logger.info(f"Attempting to login with phone {phone}...")
                    await self.client.send_code_request(phone)
                    try:
                        # Non-blocking error for plugin load
                        logger.error(f"Telegram Client needs authentication! Please authenticate via CLI or providing session file.")
                        logger.error(f"Cannot prompt for code in this environment. Please run the script in interactive mode to login once.")
                        return
                    except Exception as e:
                         logger.error(f"Login failed: {e}")
                         return
                else:
                    logger.error("No phone number provided in config. Cannot login.")
                    return

            logger.info("Telegram Forwarder: Client authorized successfully!")
            
            # Sync dialogs to ensure we can resolve channel IDs
            logger.info("Syncing dialogs...")
            await self.client.get_dialogs(limit=None)
            
        except Exception as e:
            logger.error(f"Telegram Client Error: {e}")

    def is_connected(self):
        return self.client and self.client.is_connected()
