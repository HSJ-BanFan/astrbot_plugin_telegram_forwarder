import asyncio
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger, star, AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .common.storage import Storage
from .core.client import TelegramClientWrapper
from .core.forwarder import Forwarder

class Main(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        
        # Setup Directories
        self.plugin_data_dir = os.path.join(get_astrbot_data_path(), "plugins", "astrbot_plugin_telegram_forwarder")
        if not os.path.exists(self.plugin_data_dir):
            os.makedirs(self.plugin_data_dir)
            
        # Initialize Components
        self.storage = Storage(os.path.join(self.plugin_data_dir, "data.json"))
        self.client_wrapper = TelegramClientWrapper(self.config, self.plugin_data_dir)
        self.forwarder = Forwarder(self.config, self.storage, self.client_wrapper, self.plugin_data_dir)
        
        self.scheduler = AsyncIOScheduler()

        #Start Client
        if self.client_wrapper.client:
           asyncio.create_task(self._start())

        # Warn if config missing
        if not self.config.get("api_id") or not self.config.get("api_hash"):
             logger.warning("Telegram Forwarder: api_id/api_hash missing. Please configure them.")

    async def _start(self):
        """Start client and scheduler"""
        await self.client_wrapper.start()
        
        if self.client_wrapper.is_connected():
            # Start scheduler
            if self.config.get("enabled", True):
                interval = self.config.get("check_interval", 60)
                self.scheduler.add_job(self.forwarder.check_updates, 'interval', seconds=interval)
                self.scheduler.start()
                logger.info(f"Monitoring channels: {self.config.get('source_channels')}")

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        if self.client_wrapper.client:
            await self.client_wrapper.client.disconnect()
        logger.info("Telethon Plugin Stopped")
