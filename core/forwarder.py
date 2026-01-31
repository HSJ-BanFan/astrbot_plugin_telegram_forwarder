"""
消息转发核心模块

负责从 Telegram 频道获取消息并转发到目标平台（Telegram 频道或 QQ 群）。
主要功能：
- 定期检查频道更新
- 消息过滤（关键词、正则表达式）
- 冷启动支持（从指定日期开始）
- 媒体文件下载和处理
- 多平台消息发送
"""

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
    """
    消息转发器核心类

    负责从 Telegram 源频道获取消息，处理后转发到目标平台。
    支持 Telegram-to-Telegram 和 Telegram-to-QQ 两种转发模式。
    """
    def __init__(self, config: AstrBotConfig, storage: Storage, client_wrapper: TelegramClientWrapper, plugin_data_dir: str):
        """
        初始化转发器

        Args:
            config: AstrBot 配置对象，包含源频道、目标频道、过滤规则等
            storage: 数据持久化管理器，用于记录已处理的消息ID
            client_wrapper: Telegram 客户端封装
            plugin_data_dir: 插件数据目录，用于临时存储下载的媒体文件
        """
        self.config = config
        self.storage = storage
        self.client_wrapper = client_wrapper
        self.client = client_wrapper.client  # 快捷访问
        self.plugin_data_dir = plugin_data_dir

    async def check_updates(self):
        """
        检查所有配置的频道更新

        执行流程：
        1. 检查客户端连接状态
        2. 遍历所有配置的源频道
        3. 解析频道配置（支持日期过滤）
        4. 调用 _process_channel 处理每个频道

        频道配置格式：
            - "channel_name" - 从最新消息开始
            - "channel_name|2024-01-01" - 从指定日期开始

        异常处理：
            - 单个频道处理失败不影响其他频道
            - 每个频道的错误会被记录日志
        """
        # 检查连接状态
        if not self.client_wrapper.is_connected():
            return

        # 获取源频道配置列表
        channels_config = self.config.get("source_channels", [])

        # ========== 处理每个频道 ==========
        for cfg in channels_config:
            try:
                channel_name = cfg
                start_date = None

                # 解析频道配置（支持日期过滤）
                # 格式：channel_name|YYYY-MM-DD
                if "|" in cfg:
                    channel_name, date_str = cfg.split("|", 1)
                    channel_name = channel_name.strip()
                    try:
                         # 将字符串转换为时区感知的 datetime 对象
                         start_date = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except:
                        # 日期解析失败时忽略，使用默认行为
                        pass
                else:
                    channel_name = cfg.strip()

                # 处理该频道
                await self._process_channel(channel_name, start_date)
            except Exception as e:
                # 记录错误但继续处理其他频道
                logger.error(f"Error checking {cfg}: {e}")

            # 注意：速率限制现在在每个消息处理后进行（_process_channel 内部）
            # delay = self.config.get("forward_delay", 2)
            # await asyncio.sleep(delay)

    async def _process_channel(self, channel_name: str, start_date: Optional[datetime]):
        """
        处理单个频道的消息更新

        Args:
            channel_name: 频道名称或ID
            start_date: 可选的开始日期，用于冷启动时从指定日期获取消息

        执行流程：
        1. 初始化或获取频道最后处理的消息ID
        2. 处理冷启动逻辑（首次运行）
        3. 获取新消息
        4. 应用过滤规则（关键词、正则）
        5. 逐条转发并更新持久化状态

        冷启动逻辑：
            - 有日期：从指定日期开始获取
            - 无日期：只获取最新消息ID，不处理历史
        """
        # ========== 初始化频道状态 ==========
        if not self.storage.get_channel_data(channel_name).get("last_post_id"):
             self.storage.update_last_id(channel_name, 0)  # 确保初始化

        last_id = self.storage.get_channel_data(channel_name)["last_post_id"]

        try:
            # ========== 冷启动处理 ==========
            if last_id == 0:
                if start_date:
                    # 有日期配置：从指定日期开始获取历史消息
                    logger.info(f"Cold start for {channel_name} with date {start_date}")
                    pass  # 逻辑在迭代参数中处理
                else:
                    # 无日期配置：跳过历史，只记录最新消息ID
                    # 这样可以避免首次启动时转发大量历史消息
                     msgs = await self.client.get_messages(channel_name, limit=1)
                     if msgs:
                         self.storage.update_last_id(channel_name, msgs[0].id)
                         logger.info(f"Initialized {channel_name} at ID {msgs[0].id}")
                     return

            # ========== 获取新消息 ==========
            new_messages = []

            # 构建消息迭代参数
            params = {"entity": channel_name, "reverse": True, "limit": 20}

            if last_id > 0:
                 # 常规运行：获取 ID 大于 last_id 的新消息
                 params["min_id"] = last_id
            elif start_date:
                 # 冷启动有日期：从该日期开始获取
                 params["offset_date"] = start_date
            else:
                 # 冷启动无日期：获取少量最新消息
                 params["limit"] = 5

            # 使用迭代器获取消息（支持分页，内存友好）
            async for message in self.client.iter_messages(**params):
                if not message.id: continue
                new_messages.append(message)

            # 没有新消息则返回
            if not new_messages:
                return

            # ========== 获取过滤配置 ==========
            filter_keywords = self.config.get("filter_keywords", [])
            filter_regex = self.config.get("filter_regex", "")

            final_last_id = last_id

            # ========== 处理每条消息 ==========
            for msg in new_messages:
                try:
                    # ----- 反垃圾 / 频道过滤 -----
                    # 只转发频道帖子（post=True），忽略用户消息
                    is_user_msg = isinstance(msg.from_id, PeerUser) if msg.from_id else False

                    if not msg.post and is_user_msg:
                        continue

                    text_content = msg.text or ""

                    # ----- 关键词过滤 -----
                    should_skip = False
                    if filter_keywords:
                        for kw in filter_keywords:
                            if kw in text_content:
                                logger.info(f"Filtered {msg.id}: Keyword {kw}")
                                should_skip = True
                                break

                    # ----- 正则表达式过滤 -----
                    if not should_skip and filter_regex:
                        if re.search(filter_regex, text_content, re.IGNORECASE | re.DOTALL):
                            logger.info(f"Filtered {msg.id}: Regex")
                            should_skip = True

                    # ----- 转发消息 -----
                    if not should_skip:
                         await self._forward_message(channel_name, msg)

                    # ----- 立即更新持久化状态 -----
                    # 每处理一条消息就保存，避免中途失败导致重复
                    final_last_id = max(final_last_id, msg.id)
                    self.storage.update_last_id(channel_name, final_last_id)

                    # ----- 速率限制 / 防打扰 -----
                    # 每条消息处理完后延迟，避免被平台限制
                    delay = self.config.get("forward_delay", 0)
                    if delay > 0:
                        logger.info(f"Rate limit: sleeping {delay}s...")
                        await asyncio.sleep(delay)

                except Exception as e:
                    # 单条消息处理失败不影响其他消息
                    logger.error(f"Failed to process msg {msg.id}: {e}")

        except Exception as e:
            # 频道访问错误（如无权限、频道不存在等）
            logger.error(f"Access error for {channel_name}: {e}")

    async def _forward_message(self, src_channel: str, msg: Message):
        """
        编排消息转发到所有目标平台

        Args:
            src_channel: 源频道名称
            msg: Telegram 消息对象

        Note:
            此方法是转发逻辑的入口点，按顺序调用各平台转发方法
        """
        await self._forward_to_telegram(src_channel, msg)
        await self._forward_to_qq(src_channel, msg)

    async def _forward_to_telegram(self, src_channel: str, msg: Message):
        """
        转发消息到 Telegram 目标频道

        Args:
            src_channel: 源频道名称（用于日志）
            msg: 要转发的消息对象

        转发方式：
            使用 Telethon 的 forward_messages 方法，保留原始消息的所有属性
            （媒体、转发引用、回复等）

        目标格式支持：
            - @channelname (频道名)
            - -1001234567890 (频道数字ID)
        """
        tg_target = self.config.get("target_channel")
        bot_token = self.config.get("bot_token")

        # 只有配置了目标频道和 bot_token 时才转发
        if tg_target and bot_token:
            try:
                 # ========== 解析目标频道 ==========
                 # 先解析目标实体，避免 "Cannot find entity" 错误
                 target = tg_target
                 if isinstance(target, str):
                    # 处理数字ID格式的频道
                    if target.startswith("-") or target.isdigit():
                        try:
                            target = int(target)
                        except:
                            pass

                 # 获取目标实体并转发消息
                 entity = await self.client.get_entity(target)
                 await self.client.forward_messages(entity, msg)
                 logger.info(f"Forwarded {msg.id} to TG")
            except Exception as e:
                 # 转发失败记录错误但不中断程序
                 logger.error(f"TG Forward Error: {e}")

    async def _forward_to_qq(self, src_channel: str, msg: Message):
        """
        转发消息到 QQ 群

        Args:
            src_channel: 源频道名称
            msg: Telegram 消息对象

        执行流程：
        1. 下载媒体文件（带大小检查）
        2. 清理文本内容
        3. 处理文件（小文件 base64，大文件上传托管）
        4. 构建 NapCat 消息格式
        5. 发送到 QQ 群
        6. 清理临时文件

        消息格式：
            [
                {"type": "text", "data": {"text": "消息内容"}},
                {"type": "image", "data": {"file": "base64://..."}},
                {"type": "text", "data": {"text": "[媒体链接]"}}
            ]

        NapCat API:
            使用 OneBot 11 标准协议的 send_group_msg 接口
        """
        qq_groups = self.config.get("target_qq_group")
        napcat_url = self.config.get("napcat_api_url")

        # 必须同时配置 QQ 群和 API 地址
        if not (qq_groups and napcat_url):
            return
            
        # 兼容旧配置（单个 int）
        if isinstance(qq_groups, int):
            qq_groups = [qq_groups]
        elif not isinstance(qq_groups, list):
            return

        local_files = []
        try:
             # ========== 下载媒体文件（带安全检查） ==========
            local_files = await self._download_media_safe(msg)

            # ========== 清理和准备文本 ==========
            header = f"From #{src_channel}:\n"
            cleaned_text = clean_telegram_text(msg.text or "")
            final_text = header + cleaned_text

            # 空内容检查
            if not final_text and not local_files:
                logger.info("Skipped forwarding: Empty content after cleaning")
                return

            # ========== 构建消息载荷 ==========
            message = [{"type": "text", "data": {"text": final_text}}]

            # ========== 处理文件（上传或 Base64） ==========
            for fpath in local_files:
                file_node = await self._process_one_file(fpath)
                if file_node:
                    message.append(file_node)

            # ========== 发送到 QQ ==========
            url = self.config.get("napcat_api_url", "http://127.0.0.1:3000/send_group_msg")
            async with httpx.AsyncClient() as http:
                 for gid in qq_groups:
                     if not gid: continue
                     try:
                        await http.post(url, json={"group_id": gid, "message": message}, timeout=30)
                        logger.info(f"Forwarded {msg.id} to QQ group {gid}")
                     except Exception as e:
                        logger.error(f"Failed to send to QQ group {gid}: {e}")

        except Exception as e:
            logger.error(f"QQ Forward Error: {e}")
        finally:
            # ========== 清理临时文件 ==========
            self._cleanup_files(local_files)

    async def _download_media_safe(self, msg: Message) -> list:
        """
        下载媒体文件（带大小检查）

        Args:
            msg: Telegram 消息对象

        Returns:
            list: 下载的文件路径列表

        安全措施：
            - 文件大小限制：50MB
            - 只下载图片（photo），不下载视频/文档
            - 下载进度回调（每20%输出一次）

        Note:
            为了避免下载大文件导致磁盘空间或性能问题，
            当前只支持图片类型。其他类型会跳过。
        """
        local_files = []

        # 检查消息是否包含媒体
        if not msg.media:
            return local_files

        # ========== 文件大小检查 ==========
        MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
        if hasattr(msg.media, 'document') and hasattr(msg.media.document, 'size'):
            if msg.media.document.size > MAX_FILE_SIZE:
                logger.warning(f"File too large ({msg.media.document.size} bytes), skipping download.")
                return local_files

        # ========== 判断是否应该下载 ==========
        # 当前只下载图片，不下载视频/文档
        is_photo = hasattr(msg, 'photo') and msg.photo

        should_download = is_photo

        if should_download:
             # 定义进度回调函数
             def progress_callback(current, total):
                if total > 0:
                    pct = (current / total) * 100
                    # 每 20% 输出一次进度，避免日志刷屏
                    if int(pct) % 20 == 0 and int(pct) > 0:
                        logger.info(f"Downloading {msg.id}: {pct:.1f}%")

             # 执行下载
             try:
                path = await self.client.download_media(
                    msg,
                    file=self.plugin_data_dir,
                    progress_callback=progress_callback
                )
                if path:
                    local_files.append(path)
             except Exception as e:
                logger.error(f"Download failed for msg {msg.id}: {e}")

        return local_files

    async def _process_one_file(self, fpath: str) -> Optional[dict]:
        """
        将本地文件转换为 NapCat 消息节点

        Args:
            fpath: 文件路径

        Returns:
            dict: NapCat 消息节点，格式如 {"type": "image", "data": {...}}

        处理策略：
            1. 图片文件（<5MB）：使用 Base64 编码直接嵌入
            2. 其他文件：上传到文件托管服务（如果配置）
            3. 无托管：返回文件名占位符

        支持的图片格式：
            .jpg, .jpeg, .png, .webp, .gif, .bmp
        """
        ext = os.path.splitext(fpath)[1].lower()
        hosting_url = self.config.get("file_hosting_url")

        # ========== 1. 图片 -> Base64（小文件安全） ==========
        if ext in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]:
             # 检查文件大小，Base64 对大文件不友好
            if os.path.getsize(fpath) < 5 * 1024 * 1024:
                import base64
                with open(fpath, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                # NapCat 图片消息格式
                return {"type": "image", "data": {"file": f"base64://{encoded_string}"}}
            else:
                logger.info("Image too large for base64, trying upload...")

        # ========== 2. 上传到文件托管服务 ==========
        if hosting_url:
            try:
                async with httpx.AsyncClient() as uploader:
                    # 构建 multipart/form-data 上传
                    with open(fpath, "rb") as f:
                        files = {'file': (os.path.basename(fpath), f, 'application/octet-stream')}
                        # 上传超时设置为 120 秒（适应慢速网络）
                        resp = await uploader.post(hosting_url, files=files, timeout=120)

                        if resp.status_code == 200:
                            # 提取上传后的 URL
                            link = self._extract_upload_url(resp.json(), hosting_url)
                            return {"type": "text", "data": {"text": f"\n[Media Link: {link}]"}}
                        else:
                             # 上传失败
                             return {"type": "text", "data": {"text": f"\n[Upload Failed: HTTP {resp.status_code}]"}}
            except Exception as e:
                 logger.error(f"Upload Error: {e}")
                 return {"type": "text", "data": {"text": f"\n[Media File: {os.path.basename(fpath)}] (Upload Failed)"}}

        # ========== 3. 回退方案 ==========
        # 无托管服务时，返回文件名占位符
        fname = os.path.basename(fpath)
        return {"type": "text", "data": {"text": f"\n[Media File: {fname}] (Too large/No hosting)"}}

    def _extract_upload_url(self, res_json: dict, base_url: str) -> str:
        """
        从各种托管服务响应格式中提取 URL

        Args:
            res_json: 上传接口返回的 JSON 响应
            base_url: 托管服务的 URL，用于处理相对路径

        Returns:
            str: 提取到的文件 URL

        支持的响应格式：
            1. Telegraph.ph: [{"src": "/file/..."}]
            2. 简单格式：{"url": "https://..."}
            3. 标准 API：{"code": 200, "data": {"url": "..."}}

        灵活性：
            - 自动处理相对路径和绝对路径
            - 容错处理：格式不匹配时尝试下一个提取器
        """
        # ========== 确定根 URL（用于相对路径） ==========
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        root_url = f"{parsed.scheme}://{parsed.netloc}"

        # ========== 定义 URL 提取器列表 ==========
        extractors = [
            # Telegraph.ph 格式：列表 + 相对路径
            lambda r: root_url + r[0]["src"] if isinstance(r, list) and r and "src" in r[0] else None,

            # 简单格式：直接返回 url 字段
            lambda r: r.get("url"),

            # 标准 API 格式：嵌套在 data 中
            lambda r: r.get("data", {}).get("url") if isinstance(r.get("data"), dict) else None
        ]

        # ========== 依次尝试提取器 ==========
        for extractor in extractors:
            try:
                link = extractor(res_json)
                if link:
                    # 处理相对路径
                    if link.startswith("/"):
                        return root_url + link
                    # 处理绝对路径
                    if link.startswith("http"):
                        return link
            except:
                # 当前提取器失败，继续尝试下一个
                continue

        # ========== 所有格式都不匹配 ==========
        logger.warning(f"Unknown upload response format: {res_json}")
        return "[Unknown Link]"

    def _cleanup_files(self, files: list):
        """
        清理临时下载的文件

        Args:
            files: 文件路径列表

        行为：
            - 删除每个存在的临时文件
            - 静默处理删除失败（文件可能已被其他进程占用）
        """
        for f in files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    # 删除失败时静默忽略，不中断流程
                    pass
