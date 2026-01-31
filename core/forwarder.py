import asyncio
import re
import os
import httpx
from datetime import datetime, timezone
from typing import Optional
from telethon.tl.types import Message, PeerUser

from astrbot.api import logger, AstrBotConfig
from ..common.text_tools import clean_telegram_text
from ..common.storage import Storage
from .client import TelegramClientWrapper

class Forwarder:
    def __init__(self, config: AstrBotConfig, storage: Storage, client_wrapper: TelegramClientWrapper, plugin_data_dir: str):
        self.config = config
        self.storage = storage
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client # Shortcut
        self.plugin_data_dir = plugin_data_dir

    async def check_updates(self):
        if not self.client_wrapper.is_connected():
            return

        channels_config = self.config.get("source_channels", [])
        for cfg in channels_config:
            try:
                channel_name = cfg
                start_date = None
                
                if "|" in cfg:
                    channel_name, date_str = cfg.split("|", 1)
                    channel_name = channel_name.strip()
                    try:
                         # naive string to aware datetime
                         start_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except:
                        pass
                else:
                    channel_name = cfg.strip()

                await self._process_channel(channel_name, start_date)
            except Exception as e:
                logger.error(f"Error checking {cfg}: {e}")
            
            # Rate Limiting / Do Not Disturb
            delay = self.config.get("forward_delay", 2)
            await asyncio.sleep(delay)

    async def _process_channel(self, channel_name: str, start_date: Optional[datetime]):
        if not self.storage.get_channel_data(channel_name).get("last_post_id"):
             self.storage.update_last_id(channel_name, 0) # Ensure init
        
        last_id = self.storage.get_channel_data(channel_name)["last_post_id"]
        
        try:
            # Complex Cold Start Logic
            if last_id == 0:
                if start_date:
                    logger.info(f"Cold start for {channel_name} with date {start_date}")
                    pass # logic handled in iteration params
                else:
                     # Skip history: just get the latest message ID
                     msgs = await self.client.get_messages(channel_name, limit=1)
                     if msgs:
                         self.storage.update_last_id(channel_name, msgs[0].id)
                         logger.info(f"Initialized {channel_name} at ID {msgs[0].id}")
                     return

            new_messages = []
            
            # Param Logic
            params = {"entity": channel_name, "reverse": True, "limit": 20}
            
            if last_id > 0:
                 params["min_id"] = last_id # Get messages NEWER than last_id
            elif start_date:
                 params["offset_date"] = start_date # Get messages NEWER than date
            else:
                 params["limit"] = 5
            
            async for message in self.client.iter_messages(**params):
                if not message.id: continue
                new_messages.append(message)
            
            if not new_messages:
                return

            filter_keywords = self.config.get("filter_keywords", [])
            filter_regex = self.config.get("filter_regex", "")

            final_last_id = last_id
            
            for msg in new_messages:
                try:
                    # Anti-Spam / Chat Filter
                    is_user_msg = isinstance(msg.from_id, PeerUser) if msg.from_id else False
                    
                    if not msg.post and is_user_msg:
                        continue

                    text_content = msg.text or ""
                    
                    # Filtering
                    should_skip = False
                    if filter_keywords:
                        for kw in filter_keywords:
                            if kw in text_content:
                                logger.info(f"Filtered {msg.id}: Keyword {kw}")
                                should_skip = True; break
                    
                    if not should_skip and filter_regex:
                        if re.search(filter_regex, text_content, re.IGNORECASE | re.DOTALL):
                            logger.info(f"Filtered {msg.id}: Regex")
                            should_skip = True

                    if not should_skip:
                         await self._forward_message(channel_name, msg)
                    
                    # Update persistence IMMEDIATELY after each message
                    final_last_id = max(final_last_id, msg.id)
                    self.storage.update_last_id(channel_name, final_last_id)
                    
                except Exception as e:
                    logger.error(f"Failed to process msg {msg.id}: {e}")
            
        except Exception as e:
            logger.error(f"Access error for {channel_name}: {e}")

    async def _forward_message(self, src_channel: str, msg: Message):
        header = f"From #{src_channel}:\n"
        
        # 1. To Telegram Target
        tg_target = self.config.get("target_channel")
        bot_token = self.config.get("bot_token")
        
        if tg_target and bot_token:
            try:
                 # Resolve target first to avoid "Cannot find entity" error
                 # If target is an integer ID (like -100...), we might need to get_entity first
                 target = tg_target
                 if isinstance(target, str):
                    if target.startswith("-") or target.isdigit():
                        try:
                            target = int(target)
                        except:
                            pass
                 
                 entity = await self.client.get_entity(target)
                 await self.client.forward_messages(entity, msg)
                 logger.info(f"Forwarded {msg.id} to TG")
            except Exception as e:
                 logger.error(f"TG Forward Error: {e}")

        # 2. To QQ (NapCat)
        qq_group = self.config.get("target_qq_group")
        napcat_url = self.config.get("napcat_api_url")
        
        if qq_group and napcat_url:
            local_files = []
            
            # Need to download media if any
            if msg.media:
                is_photo = hasattr(msg, 'photo') and msg.photo
                
                # Speed Optimization: User requested to ONLY download photos.
                if is_photo:
                    should_download = True
                else:
                    logger.info(f"Skipping non-photo media for {msg.id} (Speed Optimization)")
                    should_download = False

                if should_download:
                    logger.info(f"DEBUG: Downloading media for msg {msg.id}...")
                    
                    def progress_callback(current, total):
                        # Log every 10MB or 20% to avoid spam
                        if total > 0:
                            pct = (current / total) * 100
                            if int(pct) % 20 == 0 and int(pct) > 0:
                                logger.info(f"Downloading {msg.id}: {pct:.1f}% ({current}/{total} bytes)")

                    try:
                        path = await self.client.download_media(
                            msg, 
                            file=self.plugin_data_dir,
                            progress_callback=progress_callback
                        )
                        if path:
                            logger.info(f"DEBUG: Media downloaded to {path}")
                            local_files.append(path)
                    except Exception as e:
                        logger.error(f"Download failed for msg {msg.id}: {e}")
                        pass
            
            # Send to QQ
            try:
                cleaned_text = clean_telegram_text(msg.text or "")
                
                # Restore Header (From #channel:) as requested by user
                final_text = header + cleaned_text
                if not final_text and not local_files:
                    logger.info("Skipped forwarding: Empty content after cleaning")
                    return

                # Construct payload
                message = [{"type": "text", "data": {"text": final_text}}]
                
                for fpath in local_files:
                    ext = os.path.splitext(fpath)[1].lower()
                    hosting_url = self.config.get("file_hosting_url")
                    
                    if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
                        # Convert Images to Base64 (Keep existing logic for reliability)
                        import base64
                        with open(fpath, "rb") as image_file:
                            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                        message.append({"type": "image", "data": {"file": f"base64://{encoded_string}"}})
                    
                    elif hosting_url:
                        # Upload large files to hosting service
                        try:
                            logger.info(f"DEBUG: Uploading {os.path.basename(fpath)} to {hosting_url}...")
                            async with httpx.AsyncClient() as uploader:
                                with open(fpath, "rb") as f:
                                    files = {'file': (os.path.basename(fpath), f, 'application/octet-stream')}
                                    resp = await uploader.post(hosting_url, files=files, timeout=60)
                                    
                                    if resp.status_code == 200:
                                        res_json = resp.json()
                                        
                                        # Determine base URL for relative paths
                                        from urllib.parse import urlparse
                                        parsed = urlparse(hosting_url)
                                        base_url = f"{parsed.scheme}://{parsed.netloc}"

                                        # Handle Telegra.ph style [{"src": "/file/..."}]
                                        if isinstance(res_json, list) and len(res_json) > 0 and "src" in res_json[0]:
                                            link = base_url + res_json[0]["src"]
                                        
                                        # Handle {"url": "..."} (Absolute or relative)
                                        elif "url" in res_json:
                                            link = res_json["url"]
                                            if link.startswith("/"):
                                                link = base_url + link
                                        
                                        # Handle Standard API {"code": 200, "data": {"url": "..."}}
                                        elif isinstance(res_json, dict) and res_json.get("code") == 200:
                                             data = res_json.get("data", {})
                                             link = data.get("url")
                                             if link and link.startswith("/"):
                                                 link = base_url + link
                                        
                                        else:
                                            link = f"[Upload Failed: Unknown Response]"
                                            logger.warning(f"Unknown Upload Response: {res_json}")
                                        
                                        message.append({"type": "text", "data": {"text": f"\n[Media Link: {link}]"}}) 
                                    else:
                                         message.append({"type": "text", "data": {"text": f"\n[Upload Failed: HTTP {resp.status_code}]"}})
                        except Exception as e:
                             logger.error(f"Upload Error: {e}")
                             fname = os.path.basename(fpath)
                             message.append({"type": "text", "data": {"text": f"\n[Media File: {fname}] (Upload Failed)"}})

                    else:
                        # Fallback for large files if no hosting url
                        fname = os.path.basename(fpath)
                        message.append({"type": "text", "data": {"text": f"\n[Media File: {fname}] (Too large, no hosting)"}})

                url = self.config.get("napcat_api_url", "http://127.0.0.1:3000/send_group_msg")
                async with httpx.AsyncClient() as http:
                     await http.post(url, json={"group_id": qq_group, "message": message}, timeout=30)
                
                logger.info(f"Forwarded {msg.id} to QQ")
            
            except Exception as e:
                logger.error(f"QQ Forward Error: {e}")
            finally:
                # Cleanup robustly
                for fpath in local_files:
                    if os.path.exists(fpath):
                        os.remove(fpath)
