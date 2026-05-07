import asyncio
import os
import random
from collections import Counter
from datetime import datetime

from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

from ..common.text_tools import is_numeric_channel_id, normalize_telegram_channel_name


class PluginCommands:
    def __init__(
        self, context: Context, config: AstrBotConfig, forwarder, scheduler=None
    ):
        self.context = context
        self.config = config
        self.forwarder = forwarder
        self.scheduler = scheduler  # 用于 pause/resume 真正暂停调度器
        self._paused = False  # 全局暂停标志
        self.temp_data = {}

    def _find_channel_cfg(self, channel_name: str) -> dict | None:
        """根据频道名（忽略大小写）查找对应的配置项，并返回原始配置"""
        channel_name = normalize_telegram_channel_name(channel_name)
        channels = self.config.get("source_channels", [])
        for cfg in channels:
            if isinstance(cfg, dict):
                stored_name = normalize_telegram_channel_name(
                    cfg.get("channel_username", "")
                )
                if stored_name.lower() == channel_name.lower():
                    return cfg
        return None

    def _get_channel_original_name(self, channel_name: str) -> str | None:
        """根据输入（忽略大小写）返回配置文件中存储的原始频道名"""
        cfg = self._find_channel_cfg(channel_name)
        return cfg.get("channel_username") if cfg else None

    @staticmethod
    def _parse_qq_targets(raw_value: str) -> list:
        targets = []
        for item in str(raw_value).split(","):
            val = item.strip()
            if not val:
                continue
            if val.isdigit():
                targets.append(int(val))
            else:
                targets.append(val)
        return targets

    def _get_root_qq_targets(self):
        return self.config.get("target_qq_session", [])

    @staticmethod
    def _get_channel_qq_targets(channel_cfg: dict):
        return channel_cfg.get("target_qq_sessions", [])

    @staticmethod
    def _login_key(event: AstrMessageEvent) -> str:
        return f"tg_login_{event.session_id}"

    def _get_login_data(self, event: AstrMessageEvent) -> dict | None:
        return self.temp_data.get(self._login_key(event))

    def _clear_login_data(self, event: AstrMessageEvent):
        self.temp_data.pop(self._login_key(event), None)

    def _sync_login_config(self, phone: str = ""):
        """将登录流程中的关键信息回填到插件配置。"""
        changed = False
        phone_norm = self._normalize_phone(phone)
        if phone_norm and self.config.get("phone") != phone_norm:
            self.config["phone"] = phone_norm
            changed = True
        if changed:
            self.config.save_config()

    @staticmethod
    def _decode_shifted_code(obfuscated_code: str) -> str:
        """用户输入每位加 1 后的验证码，这里还原真实验证码。例: 90321 -> 89210"""
        result = []
        for ch in obfuscated_code.strip():
            if ch.isdigit():
                result.append(str((int(ch) - 1) % 10))
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        # 允许用户输入含空格-短横线的手机号，提交前统一清洗
        return (phone or "").replace(" ", "").replace("-", "").strip()

    async def handle_login(self, event: AstrMessageEvent, args: str = ""):
        raw = (args or "").strip()
        if not raw:
            yield event.plain_result(
                "用法：\n"
                "/tg login start [手机号]\n"
                "/tg login code <验证码>\n"
                "/tg login password <两步验证密码>\n"
                "/tg login status\n"
                "/tg login cancel\n"
                "/tg login reset"
            )
            return

        tokens = raw.split(maxsplit=1)
        action = tokens[0].lower()
        payload = tokens[1].strip() if len(tokens) > 1 else ""

        if action == "start":
            async for result in self._login_start(event, payload):
                yield result
            return
        if action == "code":
            async for result in self._login_code(event, payload):
                yield result
            return
        if action == "password":
            async for result in self._login_password(event, payload):
                yield result
            return
        if action == "status":
            async for result in self._login_status(event):
                yield result
            return
        if action == "cancel":
            self._clear_login_data(event)
            yield event.plain_result("已取消当前登录流程。")
            return
        if action == "reset":
            async for result in self._login_reset(event):
                yield result
            return

        yield event.plain_result(
            "未知子命令。可用：start / code / password / status / cancel / reset"
        )

    async def _login_start(self, event: AstrMessageEvent, phone_arg: str):
        wrapper = self.forwarder.client_wrapper
        if not wrapper or not wrapper.client:
            yield event.plain_result(
                "Telegram 客户端未就绪，请先配置 api_id / api_hash。"
            )
            return

        try:
            await wrapper.ensure_connected()
            if await wrapper.client.is_user_authorized():
                await wrapper._mark_authorized_if_needed()
                me = None
                try:
                    me = await wrapper.client.get_me()
                except Exception:
                    pass
                self._sync_login_config(getattr(me, "phone", "") if me else "")
                self._clear_login_data(event)
                yield event.plain_result(
                    "当前 Telegram 账号已授权。\n"
                    "如需切换账号，请先执行：/tg login reset"
                )
                return
        except Exception as e:
            logger.error(f"[Login] check authorized failed: {e}")
            yield event.plain_result(f"连接 Telegram 失败：{e}")
            return

        phone = self._normalize_phone(phone_arg or (self.config.get("phone") or ""))
        if not phone:
            yield event.plain_result(
                "请提供手机号，例如：/tg login start +8613812345678"
            )
            return

        try:
            phone_code_hash = await wrapper.send_login_code(phone)
            self._sync_login_config(phone)
            self.temp_data[self._login_key(event)] = {
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "need_password": False,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            yield event.plain_result(
                "验证码已发送。\n"
                "下一步：/tg login code <验证码>\n"
                "注意：请输入每位加 1 后的验证码。\n"
                "若开启两步验证：/tg login password <两步验证密码>"
            )
        except FloodWaitError as e:
            wait_seconds = getattr(e, "seconds", 0) or 0
            yield event.plain_result(f"请求过于频繁，请等待 {wait_seconds} 秒后重试。")
        except Exception as e:
            logger.error(f"[Login] send code failed: {e}")
            yield event.plain_result(f"发送验证码失败：{e}")

    async def _login_code(self, event: AstrMessageEvent, code: str):
        if not code:
            yield event.plain_result("请提供验证码，例如：/tg login code 12345")
            return

        login_data = self._get_login_data(event)
        if not login_data:
            yield event.plain_result(
                "当前没有进行中的登录流程，请先执行 /tg login start。"
            )
            return

        wrapper = self.forwarder.client_wrapper
        try:
            # 用户输入的是“每位加 1 后”的验证码，这里还原后再提交
            real_code = self._decode_shifted_code(code)
            ok, _ = await wrapper.sign_in_with_code(
                phone=login_data["phone"],
                code=real_code,
                phone_code_hash=login_data.get("phone_code_hash", ""),
            )
            if ok:
                self._sync_login_config(login_data.get("phone", ""))
                self._clear_login_data(event)
                yield event.plain_result("登录成功。")
                return
            yield event.plain_result("验证码已提交，但账号仍未授权。")
        except SessionPasswordNeededError:
            login_data["need_password"] = True
            self.temp_data[self._login_key(event)] = login_data
            yield event.plain_result(
                "该账号已开启两步验证，请执行：/tg login password <两步验证密码>"
            )
        except PhoneCodeInvalidError:
            yield event.plain_result("验证码错误。")
        except PhoneCodeExpiredError:
            self._clear_login_data(event)
            yield event.plain_result("验证码已过期，请重新执行 /tg login start。")
        except FloodWaitError as e:
            wait_seconds = getattr(e, "seconds", 0) or 0
            yield event.plain_result(f"请求过于频繁，请等待 {wait_seconds} 秒后重试。")
        except Exception as e:
            logger.error(f"[Login] submit code failed: {e}")
            yield event.plain_result(f"提交验证码失败：{e}")

    async def _login_password(self, event: AstrMessageEvent, password: str):
        if not password:
            yield event.plain_result(
                "请提供两步验证密码，例如：/tg login password your_password"
            )
            return

        login_data = self._get_login_data(event)
        if not login_data:
            yield event.plain_result(
                "当前没有进行中的登录流程，请先执行 /tg login start。"
            )
            return

        wrapper = self.forwarder.client_wrapper
        try:
            ok = await wrapper.sign_in_with_password(password.strip())
            if ok:
                self._sync_login_config(login_data.get("phone", ""))
                self._clear_login_data(event)
                yield event.plain_result("两步验证通过，登录完成。")
            else:
                yield event.plain_result("密码已提交，但账号仍未授权。")
        except FloodWaitError as e:
            wait_seconds = getattr(e, "seconds", 0) or 0
            yield event.plain_result(f"请求过于频繁，请等待 {wait_seconds} 秒后重试。")
        except Exception as e:
            logger.error(f"[Login] submit password failed: {e}")
            yield event.plain_result(f"提交两步验证密码失败：{e}")

    async def _login_status(self, event: AstrMessageEvent):
        wrapper = self.forwarder.client_wrapper
        if not wrapper or not wrapper.client:
            yield event.plain_result("Telegram 客户端未初始化。")
            return

        login_data = self._get_login_data(event)
        connected = wrapper.is_connected()
        authorized = wrapper.is_authorized()
        if connected and not authorized:
            try:
                authorized = await wrapper.client.is_user_authorized()
                if authorized:
                    wrapper._authorized = True
            except Exception:
                pass
        yield event.plain_result(
            "登录状态：\n"
            f"- 已连接：{connected}\n"
            f"- 已授权：{authorized}\n"
            f"- 登录流程进行中：{bool(login_data)}\n"
            f"- 等待两步验证密码：{bool(login_data and login_data.get('need_password'))}\n"
            f"- 当前流程手机号：{(login_data or {}).get('phone', '-')}"
        )

    async def _login_reset(self, event: AstrMessageEvent):
        wrapper = self.forwarder.client_wrapper
        if not wrapper:
            yield event.plain_result("Telegram 客户端包装器未初始化。")
            return

        self._clear_login_data(event)

        try:
            if wrapper.client and wrapper.client.is_connected():
                await wrapper.client.disconnect()
        except Exception as e:
            logger.debug(f"[Login] disconnect during reset failed: {e}")

        try:
            session_path = os.path.join(wrapper.plugin_data_dir, "user_session")
            wrapper.clear_cache(session_path)

            # 删除磁盘上的 session 相关文件（如果存在）
            for suffix in (
                ".session",
                ".session-journal",
                ".session-shm",
                ".session-wal",
            ):
                p = f"{session_path}{suffix}"
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception as e:
                        logger.debug(f"[Login] remove session file failed {p}: {e}")

            # 重新初始化客户端，确保不复用旧连接
            wrapper.client = None
            wrapper._authorized = False
            wrapper._init_client()

            yield event.plain_result(
                "登录状态已重置。\n请执行：/tg login start <手机号>"
            )
        except Exception as e:
            logger.error(f"[Login] reset failed: {e}")
            yield event.plain_result(f"重置失败：{e}")

    async def add_channel(self, event: AstrMessageEvent, channel: str):
        """添加监控频道"""
        if not channel:
            yield event.plain_result("❌ 用法：/tg add <频道用户名> （不带@）")
            return

        channel_clean = channel.lstrip("@#").strip()
        channels = self.config.get("source_channels", [])

        # 检查是否已存在（忽略大小写）
        exists = any(
            isinstance(c, dict)
            and c.get("channel_username", "").lower() == channel_clean.lower()
            for c in channels
        )

        if exists:
            original_name = self._get_channel_original_name(channel_clean)
            yield event.plain_result(
                f"⚠️ 频道 @{original_name or channel_clean} 已在监控列表中。"
            )
            return

        new_item = {
            "__template_key": "default",
            "channel_username": channel_clean,  # 统一保存清理后的名字（不带@）
            "start_time": "",
            "check_interval": 0,
            "msg_limit": 10,
            "priority": 0,
            "forward_types": ["文字", "图片", "视频", "音频", "文件"],
            "max_file_size": 0,
        }
        channels.append(new_item)
        self.config["source_channels"] = channels
        self.config.save_config()
        yield event.plain_result(f"✅ 已添加监控频道 @{channel_clean}")

    async def remove_channel(self, event: AstrMessageEvent, channel: str):
        """移除监控频道"""
        if not channel:
            yield event.plain_result("❌ 用法：/tg rm <频道用户名> （不带@）")
            return

        channel_clean = channel.lstrip("@#").strip()
        channels = self.config.get("source_channels", [])
        target_index = -1

        for i, c in enumerate(channels):
            if (
                isinstance(c, dict)
                and c.get("channel_username", "").lower() == channel_clean.lower()
            ):
                target_index = i
                break

        if target_index == -1:
            yield event.plain_result(f"⚠️ 频道 @{channel_clean} 不在监控列表中。")
            return

        removed_name = channels[target_index].get("channel_username", channel_clean)
        channels.pop(target_index)
        self.config["source_channels"] = channels
        self.config.save_config()
        yield event.plain_result(f"✅ 已移除监控频道 @{removed_name}")

    async def list_channels(self, event: AstrMessageEvent):
        """列出所有监控频道"""
        channels = self.config.get("source_channels", [])
        if not channels:
            yield event.plain_result("📭 当前没有任何监控频道。")
            return

        lines = ["📺 监控中的频道："]
        for c in channels:
            if isinstance(c, dict):
                name = c.get("channel_username", "??")
                start = c.get("start_time") or "实时"
                display = name if is_numeric_channel_id(name) else f"@{name}"
                lines.append(f"  • {display}  (从 {start} 开始)")
            else:
                display = c if is_numeric_channel_id(c) else f"@{c}"
                lines.append(f"  • {display}")

        yield event.plain_result("\n".join(lines))

    async def force_check(self, event: AstrMessageEvent):
        """立即触发一次全频道检查 & 发送"""
        if self._paused:
            yield event.plain_result("⏸️ 插件当前处于暂停状态，请先 /tg resume")
            return

        yield event.plain_result("🔄 正在触发全量检查更新...")
        asyncio.create_task(self.forwarder.check_updates())
        asyncio.create_task(self.forwarder.send_pending_messages())

    async def show_status(self, event: AstrMessageEvent):
        """查看插件运行状态（已合并统计信息）"""
        lines = ["📊 Telegram Forwarder 状态"]
        lines.append("─" * 13)

        # 客户端状态
        if not self.forwarder.client_wrapper.client:
            lines.append("• Telegram 客户端：❌ 未初始化（缺少 api_id/api_hash？）")
        else:
            auth = (
                "已授权" if self.forwarder.client_wrapper.is_authorized() else "未授权"
            )
            conn = "已连接" if self.forwarder.client_wrapper.is_connected() else "断开"
            lines.append(f"• Telegram 客户端：{auth} / {conn}")

        # 运行状态
        lines.append(f"• 全局运行状态：{'暂停' if self._paused else '正常'}")

        # 监控频道数量
        channels = self.config.get("source_channels", [])
        active = len(
            [c for c in channels if isinstance(c, dict) and c.get("channel_username")]
        )
        lines.append(f"• 监控频道数量：{active} 个")

        # 转发统计
        s = self.forwarder.stats
        lines.append(f"• 已尝试转发消息：{s['forward_attempts']} 条")
        lines.append(f"• 成功转发：{s['forward_success']} 条")
        lines.append(f"• 转发失败：{s['forward_failed']} 条")
        if s["forward_attempts"] > 0:
            rate = s["forward_success"] / s["forward_attempts"] * 100
            lines.append(f"• 成功率：{rate:.1f}%")
        lines.append(f"• 统计开始时间：{s['last_reset']}")

        # 待发送队列统计
        all_pending = self.forwarder.storage.get_all_pending()
        total = len(all_pending)
        if total == 0:
            lines.append("• 待发送队列：空")
        else:
            lines.append(f"• 待发送队列：{total} 条")
            cnt = Counter(item["channel"] for item in all_pending)
            for ch, n in sorted(cnt.items(), key=lambda x: x[1], reverse=True):
                display = ch if is_numeric_channel_id(ch) else f"@{ch.lstrip('@')}"
                lines.append(f"  - {display}: {n} 条")

        yield event.plain_result("\n".join(lines))

    async def pause(self, event: AstrMessageEvent):
        """暂停抓取和发送（暂停调度器）"""
        if self._paused:
            yield event.plain_result("⚠️ 插件已经处于暂停状态。")
            return

        self._paused = True
        self.forwarder._stopping = True

        if self.scheduler and self.scheduler.running:
            self.scheduler.pause()
            logger.info("[Commands] 调度器已暂停")

        yield event.plain_result("⏸️ 已暂停抓取与发送。使用 /tg resume 恢复。")

    async def resume(self, event: AstrMessageEvent):
        """恢复抓取和发送（恢复调度器）"""
        if not self._paused:
            yield event.plain_result("ℹ️ 插件当前并未暂停。")
            return

        self._paused = False
        self.forwarder._stopping = False

        if self.scheduler:
            if not self.scheduler.running:
                self.scheduler.start()
            else:
                self.scheduler.resume()
            logger.info("[Commands] 调度器已恢复")

        yield event.plain_result("▶️ 已恢复抓取与发送。")

    async def show_queue(self, event: AstrMessageEvent):
        """查看当前待发送队列概览"""
        all_pending = self.forwarder.storage.get_all_pending()
        total = len(all_pending)
        if total == 0:
            yield event.plain_result("📭 待发送队列为空。")
            return

        cnt = Counter(item["channel"] for item in all_pending)

        lines = [f"📬 待发送队列（共 {total} 条）："]
        for ch, n in sorted(cnt.items(), key=lambda x: x[1], reverse=True):
            display = ch if is_numeric_channel_id(ch) else f"@{ch.lstrip('@')}"
            lines.append(f"  • {display}: {n} 条")

        yield event.plain_result("\n".join(lines))

    async def clear_queue(self, event: AstrMessageEvent, target: str | None = None):
        """清空待发送队列   用法：/tg clearqueue [频道|all]"""
        if not target:
            yield event.plain_result(
                "❔ 用法：\n"
                "/tg clearqueue all          清空所有频道队列\n"
                "/tg clearqueue <频道名>     只清空指定频道队列"
            )
            return

        all_pending = self.forwarder.storage.get_all_pending()
        if not all_pending:
            yield event.plain_result("📭 队列已经是空的。")
            return

        target = target.strip().lower()
        if target == "all":
            for ch_data in self.forwarder.storage.persistence["channels"].values():
                ch_data["pending_queue"] = []
            self.forwarder.storage.save()
            yield event.plain_result("🗑️ 已清空**所有**频道的待发送队列。")
        else:
            channel_name = target.lstrip("@#")
            cfg = self._find_channel_cfg(channel_name)
            if not cfg:
                yield event.plain_result(f"❌ 未找到监控中的频道 @{channel_name}")
                return

            original_name = cfg.get("channel_username")
            data = self.forwarder.storage.get_channel_data(original_name)
            old_len = len(data["pending_queue"])
            if old_len == 0:
                disp = (
                    original_name
                    if is_numeric_channel_id(original_name)
                    else f"@{original_name}"
                )
                yield event.plain_result(f"{disp} 的队列已经是空的。")
            else:
                data["pending_queue"] = []
                self.forwarder.storage.save()
                disp = (
                    original_name
                    if is_numeric_channel_id(original_name)
                    else f"@{original_name}"
                )
                yield event.plain_result(
                    f"🗑️ 已清空 {disp} 的待发送队列（{old_len} 条）。"
                )

    def mask_sensitive(self, value, field_name: str, mask_ratio: float = 0.5) -> str:
        """
        对敏感字段进行随机位置的 * 替换，大约遮掉 mask_ratio 比例的字符
        非敏感字段或短值保持原样或简单处理
        """
        if value is None or value == "":
            return "<未设置>"

        s = str(value).strip()
        if len(s) <= 4:
            return "*" * len(s)  # 太短直接全遮

        sensitive_fields = {"api_id", "api_hash", "phone", "proxy"}

        if field_name not in sensitive_fields:
            return s

        # 至少保留首尾各1个字符（如果够长）
        if len(s) <= 6:
            return s[0] + "*" * (len(s) - 2) + s[-1] if len(s) > 2 else "*" * len(s)

        # 计算要遮罩的字符数（至少1个）
        mask_count = max(1, int(len(s) * mask_ratio + 0.5))

        # 可被遮罩的位置（排除首尾）
        positions = list(range(1, len(s) - 1))
        random.shuffle(positions)
        mask_positions = set(positions[:mask_count])

        result = []
        for i, char in enumerate(s):
            if i in mask_positions:
                result.append("*")
            else:
                result.append(char)

        return "".join(result)

    async def get_config(self, event: AstrMessageEvent, target: str = None):
        """查看频道、全局或根配置"""
        if not target:
            yield event.plain_result(
                "用法：\n"
                "  /tg get global            查看全局转发配置 (forward_config)\n"
                "  /tg get root              查看插件根级别配置（target_xxx、api等）\n"
                "  /tg get @频道名           查看指定频道的配置\n"
            )
            return

        args = target.strip().split()
        target_clean = args[0].lstrip("@").strip().lower()

        # ─── root 模式 ───
        if target_clean == "root":
            interesting_root_keys = [
                "target_qq_session",
                "target_channel",
                "phone",
                "api_id",
                "api_hash",
                "telegram_session",
                "proxy",
                "debug_enabled_default",
            ]
            root_display = {}
            for key in interesting_root_keys:
                if key == "target_qq_session":
                    val = self._get_root_qq_targets()
                else:
                    val = self.config.get(key)
                if key == "telegram_session":
                    if isinstance(val, list):
                        if not val:
                            display = "未设置"
                        else:
                            display = f"已上传 {len(val)} 个 session 文件"
                    else:
                        display = "格式异常"
                elif key == "debug_enabled_default":
                    display = str(bool(val)) if val is not None else "<未设置>"
                else:
                    display = self.mask_sensitive(val, key)  # 脱敏

                root_display[key] = display

            # 已脱敏
            lines = ["【插件根级别配置概览】", "─────────────"]
            for k, display in root_display.items():
                lines.append(f"• {k:<18} : {display}")

            yield event.plain_result("\n".join(lines))
            return

        # ─── 原有 global / 频道 ───
        is_global = target_clean == "global"

        if is_global:
            cfg = self.config.get("forward_config", {})
            title = "全局转发配置（forward_config）"
        else:
            ch_cfg = self._find_channel_cfg(target_clean)
            if not ch_cfg:
                yield event.plain_result(f"❌ 未找到频道 @{target_clean}")
                return
            cfg = ch_cfg
            title = f"频道 @{ch_cfg.get('channel_username')} 配置"

        lines = [f"【{title} 概览】", "─────────────"]

        def add_kv(k, display_name, default_value=None, suffix=""):
            v = cfg.get(k, default_value)
            lines.append(
                f"• {display_name:<12} : {v if v is not None else '<未设置>'}{suffix}"
            )

        # ─── 通用字段（优先级、检测间隔、抓取上限、起始时间） ───
        common_fields = [
            ("priority", "优先级", 0, ""),
            ("check_interval", "检测间隔(秒)", 0, ""),
            ("msg_limit", "单次抓取上限", 10, "条"),
            ("start_time", "起始时间", "实时", ""),
        ]

        for key, name, default, unit in common_fields:
            raw_value = cfg.get(key, default)
            display_value = raw_value
            suffix = ""

            if not is_global:
                # 只有频道模式才判断是否继承全局
                if key == "check_interval" and raw_value in (0, None, ""):
                    global_val = self.config.get("forward_config", {}).get(
                        "check_interval", 60
                    )
                    suffix = f"（继承全局 {global_val}秒）"
                    display_value = global_val
                elif key == "msg_limit" and raw_value in (0, None, ""):
                    global_val = self.config.get("forward_config", {}).get(
                        "msg_limit", 10
                    )
                    suffix = f"（继承全局 {global_val}条）"
                    display_value = global_val
                elif raw_value is None or raw_value == "":
                    display_value = "<未设置/继承全局>"
            else:
                # 全局模式：显示原始值，不加继承提示
                if raw_value is None or raw_value == "":
                    display_value = "<未设置>"

            lines.append(f"• {name:<12} : {display_value}{unit}{suffix}")

        # 特殊处理列表类
        def format_list_field(key, name, val_type=False):
            lst = cfg.get(key, [])
            cnt = len(lst)
            if cnt == 0:
                val = "无"
            elif val_type:
                val = "、".join(str(x) for x in lst)
            else:
                val = f"{cnt} 个"
            lines.append(f"• {name:<12} : {val}")

        format_list_field("forward_types", "转发类型", val_type=True)
        format_list_field("filter_keywords", "过滤关键词")
        format_list_field("monitor_keywords", "监听关键词")

        # 正则只显示是否存在
        for key, name in [("filter_regex", "正则过滤"), ("monitor_regex", "监听正则")]:
            val = "已启用" if cfg.get(key) else "未设置"
            lines.append(f"• {name:<12} : {val}")

        # ─── 继承全局的开关字段 ───
        inherit_fields = [
            "exclude_text_on_media",
            "filter_spoiler_messages",
            "strip_markdown_links",
        ]

        for key in inherit_fields:
            name_map = {
                "exclude_text_on_media": "媒体消息仅发送媒体",
                "filter_spoiler_messages": "过滤剧透消息",
                "strip_markdown_links": "剥离MD链接只留文字",
            }
            display_name = name_map.get(key, key.replace("_", " ").title())

            # 获取原始值
            raw = cfg.get(key)
            if raw is None:
                raw_str = "继承全局"
            elif isinstance(raw, bool):
                raw_str = "开启" if raw else "关闭"
            elif raw == "继承全局":
                raw_str = "继承全局"
            else:
                raw_str = str(raw)

            if is_global:
                # 全局模式：原始值即实际值
                lines.append(f"• {display_name:<12} : {raw_str}")
            else:
                # 频道模式：显示原始值 + 实际生效值
                effective = self.forwarder._get_effective_config(
                    ch_cfg.get("channel_username")
                )[key]
                eff_str = "开启" if effective else "关闭"
                suffix = f"（{eff_str}）" if raw != effective else ""
                lines.append(f"• {display_name:<12} : {raw_str}{suffix}")

        if not is_global:
            channel_targets = self._get_channel_qq_targets(ch_cfg)
            if channel_targets:
                target_val = "、".join(str(x) for x in channel_targets)
            else:
                target_val = "无"
            lines.append(f"• {'专属 QQ 目标':<12} : {target_val}")

        yield event.plain_result("\n".join(lines))

    def show_set_help_for_target(self, target: str) -> str:
        """生成 /tg set <目标> 的完整字段帮助文本"""
        target_clean = target.lstrip("@").lower()
        lines = []

        if target_clean == "root":
            lines.append("【root 模式 - 可修改的根级别配置】")
            lines.append("─────────────")
            help_items = [
                (
                    "target_qq_session",
                    "QQ 目标会话列表（逗号分隔，支持群号或完整会话名）",
                ),
                ("target_channel", "TG 目标频道（@xxx 或 -100xxxx，多个用逗号分隔）"),
                ("phone", "Telegram 登录手机号（国际格式，如 +86138xxxxxxxx）"),
                ("api_id", "Telegram API ID（纯数字，从 my.telegram.org 获取）"),
                ("api_hash", "Telegram API Hash（字符串，从 my.telegram.org 获取）"),
                ("proxy", "代理地址（例如 http://127.0.0.1:7890 或 socks5://...）"),
                ("debug_enabled_default", "QQ 发送诊断日志默认开关(true/false)"),
            ]

        elif target_clean == "global":
            lines.append("【global 模式 - 全局转发配置 (forward_config)】")
            lines.append("─────────────")
            help_items = [
                ("check_interval", "检测新消息的间隔（秒，默认60）"),
                ("send_interval", "从待发队列实际发送的间隔（秒，默认60）"),
                ("batch_size_limit", "单次发送最多几条消息（建议1~20，默认3）"),
                ("qq_merge_threshold", "QQ 合并转发阈值（≥此值打包合并，≤1=永不合并）"),
                ("retention_period", "待发消息最长保留时间（秒，超期丢弃，默认86400）"),
                ("max_file_size", "非图片媒体大小上限（MB，0=不限制）"),
                ("apk_fallback_mode", "APK 发送失败兜底模式（关闭/直链/压缩包/直链优先失败转压缩包）"),
                ("apk_direct_link_base_url", "APK 直链基址（仅在直链模式下生效）"),
                (
                    "exclude_text_on_media",
                    "媒体消息是否只发媒体不带文字（true/false/开启/关闭）",
                ),
                ("filter_spoiler_messages", "是否过滤带有剧透标记的消息"),
                ("strip_markdown_links", "是否把 [文字](链接) 剥离成纯文字，丢弃链接"),
                ("enable_deduplication", "是否开启转发查重（避免重复转发）"),
                ("use_channel_title", "From 头部是否显示频道名称而非数字ID"),
                (
                    "forward_types",
                    "允许转发的消息类型（文字,图片,视频,音频,文件 逗号分隔）",
                ),
                ("filter_keywords", "全局过滤关键词（包含任意一个即丢弃，逗号分隔）"),
                ("filter_regex", "全局正则过滤（Python re 语法）"),
                ("monitor_keywords", "全局监听关键词（命中任一立即触发）"),
                ("monitor_regex", "全局监听正则（命中立即触发）"),
                ("curfew_time", "宵禁时间段（格式 23:00-07:00，支持跨天，留空禁用）"),
            ]

        else:
            if target_clean == "all":
                channel_name = target_clean
            else:
                ch_cfg = self._find_channel_cfg(target_clean)
                if not ch_cfg:
                    return f"❌ 未找到监控中的频道 @{target_clean}\n请先使用 /tg add {target_clean} 添加该频道。"
                channel_name = ch_cfg.get("channel_username", target_clean)
            lines.append(f"【频道 @{channel_name} - 可修改的专属配置】")
            lines.append("─────────────")
            help_items = [
                ("priority", "优先级（数字越大越优先，建议 ≥1，0=最低）"),
                ("check_interval", "本频道检测间隔（秒，0=使用全局）"),
                ("msg_limit", "单次最多抓取的消息条数（0=使用全局）"),
                (
                    "start_time",
                    "从哪一天开始补发历史消息（YYYY-MM-DD，留空=只抓新消息）",
                ),
                (
                    "target_qq_sessions",
                    "本频道专属 QQ 目标会话（支持群号或完整会话名，逗号分隔，留空=使用全局）",
                ),
                (
                    "forward_types",
                    "本频道允许转发的消息类型（文字,图片,视频,音频,文件）",
                ),
                ("max_file_size", "本频道非图片媒体大小上限（MB，0=不限制）"),
                (
                    "exclude_text_on_media",
                    "媒体消息仅发送媒体（继承全局 / 开启 / 关闭）",
                ),
                (
                    "filter_spoiler_messages",
                    "是否过滤剧透消息（继承全局 / 开启 / 关闭）",
                ),
                (
                    "strip_markdown_links",
                    "是否剥离 Markdown 链接（继承全局 / 开启 / 关闭）",
                ),
                (
                    "ignore_global_filters",
                    "是否忽略全局的关键词/正则过滤（true/false）",
                ),
                ("filter_keywords", "本频道专属过滤关键词（逗号分隔）"),
                ("filter_regex", "本频道专属正则过滤"),
                ("monitor_keywords", "本频道专属监听关键词（命中立即抓取）"),
                ("monitor_regex", "本频道专属监听正则"),
            ]

        for field, desc in help_items:
            lines.append(f"  {field:<25}  {desc}")

        lines.append("\n常用操作提示：")
        lines.append("• 列表字段使用英文逗号分隔，不要加空格或引号")
        lines.append("• 清空列表字段：写 [] 或 清空 或 none 或 empty")
        lines.append("• 布尔值支持写法：true/false/1/0/开启/关闭/是/否/开/关")

        return "\n".join(lines)

    def _get_single_field_help(self, target: str, field: str) -> str:
        """返回单个字段的简短格式说明，用于错误提示"""
        target_clean = target.lstrip("@").lower()

        if target_clean == "root":
            mapping = {
                "target_qq_session": "列表，例如：123456,平台ID:GroupMessage:123456,平台ID:FriendMessage:123456",
                "target_channel": "频道ID或用户名，例如：@mychannel,-100123456789",
                "phone": "手机号，例如：+8613812345678",
                "api_id": "纯数字，例如：1234567",
                "api_hash": "字符串，例如：a1b2c3d4e5f6g7h8i9j0",
                "proxy": "代理地址，例如：http://127.0.0.1:7890",
                "debug_enabled_default": "true / false / 开启 / 关闭",
            }
        elif target_clean == "global":
            mapping = {
                "check_interval": "数字（秒），例如 60、120",
                "send_interval": "数字（秒），例如 60",
                "batch_size_limit": "数字（建议1~20），例如 5",
                "qq_merge_threshold": "数字（≤1不合并），例如 8",
                "retention_period": "秒数，例如 86400",
                "max_file_size": "MB（0=不限），例如 50",
                "exclude_text_on_media": "true / false / 开启 / 关闭",
                "filter_spoiler_messages": "true / false / 开启 / 关闭",
                "strip_markdown_links": "true / false / 开启 / 关闭",
                "enable_deduplication": "true / false / 开启 / 关闭",
                "use_channel_title": "true / false / 开启 / 关闭",
                "forward_types": "文字,图片,视频,音频,文件（逗号分隔）",
                "filter_keywords": "关键词1,关键词2,广告",
                "filter_regex": "正则表达式，例如 ^(测试|广告)",
                "monitor_keywords": "关键词1,关键词2",
                "monitor_regex": "正则表达式",
                "curfew_time": "时间段，例如 23:00-07:00 或留空",
            }
        else:
            mapping = {
                "priority": "整数，例如 5",
                "check_interval": "秒数（0=用全局），例如 30",
                "msg_limit": "条数（0=用全局），例如 10",
                "start_time": "日期 YYYY-MM-DD 或留空",
                "target_qq_sessions": "列表，例如 123456,平台ID:GroupMessage:123456,平台ID:FriendMessage:123456 或留空",
                "forward_types": "文字,图片,视频,音频,文件",
                "max_file_size": "MB（0=不限），例如 20",
                "exclude_text_on_media": "继承全局 / 开启 / 关闭",
                "filter_spoiler_messages": "继承全局 / 开启 / 关闭",
                "strip_markdown_links": "继承全局 / 开启 / 关闭",
                "ignore_global_filters": "true / false",
                "filter_keywords": "关键词1,关键词2",
                "filter_regex": "正则表达式",
                "monitor_keywords": "关键词1,关键词2",
                "monitor_regex": "正则表达式",
            }

        desc = mapping.get(field, "（格式要求请参考完整帮助）")
        return f"  {field} → {desc}"

    async def set_config(self, event: AstrMessageEvent, args: str = ""):
        if not args:
            yield event.plain_result(
                "用法：/tg set <目标> <字段> <值>\n\n"
                "目标支持：root / global / @频道名 / all\n"
                "  all → 一次性批量修改**所有监控频道**的该字段\n"
                "只输入目标可查看该目标支持的字段列表\n"
                "示例：  /tg set global\n"
                "        /tg set @MyChannel priority 5\n"
                "        /tg set all check_interval 180"
            )
            return

        parts = args.split(maxsplit=3)
        target = parts[0].strip().lower()

        # ────────────────────────────── 处理 all 模式 ──────────────────────────────
        # ─── 处理二次确认 ───
        if len(parts) == 2 and parts[1].strip().lower() == "confirm":
            confirm_key = f"set_all_confirm_{event.session_id}"
            confirm_data = self.temp_data.get(confirm_key)

            if not confirm_data:
                yield event.plain_result("❌ 未找到待确认的批量设置操作，或已超时。")
                return

            elapsed = datetime.now().timestamp() - confirm_data["timestamp"]
            if elapsed > 30:
                del self.temp_data[confirm_key]
                yield event.plain_result(
                    "❌ 操作已超时（30秒），请重新执行 /tg set all ..."
                )
                return

            # 执行批量修改
            field = confirm_data["field"]
            value_str = confirm_data["value_str"]
            channel_count = confirm_data["channel_count"]

            del self.temp_data[confirm_key]  # 清理

            channels = self.config.get("source_channels", [])
            modified_count = 0
            error_lines = []

            field_handlers = {  # 重复定义一次，避免依赖外部状态
                "priority": int,
                "check_interval": int,
                "msg_limit": int,
                "start_time": str,
                "target_qq_sessions": self._parse_qq_targets,
                "forward_types": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "max_file_size": float,
                "apk_fallback_mode": str,
                "apk_direct_link_base_url": str,
                "exclude_text_on_media": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "filter_spoiler_messages": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "strip_markdown_links": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "ignore_global_filters": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "filter_keywords": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "filter_regex": str,
                "monitor_keywords": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "monitor_regex": str,
            }

            handler = field_handlers[field]
            raw_lower = value_str.strip().lower()
            is_clear_cmd = raw_lower in ("[]", "清空", "clear", "none", "empty", "null")

            for ch_cfg in channels:
                if not isinstance(ch_cfg, dict) or not ch_cfg.get("channel_username"):
                    continue
                channel_name = ch_cfg.get("channel_username")
                target_name = (
                    channel_name
                    if is_numeric_channel_id(channel_name)
                    else f"@{channel_name}"
                )

                try:
                    if is_clear_cmd and field in (
                        "forward_types",
                        "filter_keywords",
                        "monitor_keywords",
                        "target_qq_sessions",
                    ):
                        value = []
                    else:
                        value = handler(value_str)
                    old = ch_cfg.get(field, "<未设置>")
                    ch_cfg[field] = value
                    modified_count += 1
                except Exception as e:
                    error_lines.append(f"  • {target_name} 设置失败：{str(e)[:60]}")

            self.config["source_channels"] = channels
            self.config.save_config()

            summary = f"批量修改完成：成功更新 {modified_count} / {channel_count} 个频道\n字段：{field}\n新值：{value_str}"
            if error_lines:
                summary += "\n以下频道失败：\n" + "\n".join(error_lines)

            yield event.plain_result(
                f"✅ {summary}\n配置已保存。下次调度自动生效，也可 /tg check 立即触发。"
            )

            plugin_name = "astrbot_plugin_telegram_forwarder"
            success, err = await self.context._star_manager.reload(plugin_name)
            msg = (
                "\n已自动重载插件，变更全面生效。"
                if success
                else f"\n自动重载失败：{err}"
            )
            yield event.plain_result(msg)
            return

        if target == "all":
            channels = self.config.get("source_channels", [])
            valid_channels = [
                cfg
                for cfg in channels
                if isinstance(cfg, dict) and cfg.get("channel_username")
            ]

            if not valid_channels:
                yield event.plain_result("❌ 当前没有任何监控频道，无法批量设置。")
                return

            if len(parts) < 3:
                # 只输入 /tg set all → 显示帮助
                help_text = self.show_set_help_for_target("all")  # 频道帮助
                yield event.plain_result(
                    "【all 模式 - 批量修改所有监控频道】\n"
                    "─────────────\n"
                    f"共 {len(valid_channels)} 个频道将被影响\n\n"
                    "支持的字段与单个频道相同：\n\n"
                    + help_text.split("─────────────\n")[-1]
                )
                return

            field = parts[1].strip()
            value_str = " ".join(parts[2:]).strip()

            # ─── 字段校验（提前检查是否支持，避免确认后才报错） ───
            field_handlers = {
                "priority": int,
                "check_interval": int,
                "msg_limit": int,
                "start_time": str,
                "target_qq_sessions": self._parse_qq_targets,
                "forward_types": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "max_file_size": float,
                "exclude_text_on_media": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "filter_spoiler_messages": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "strip_markdown_links": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "ignore_global_filters": lambda v: (
                    v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
                ),
                "filter_keywords": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "filter_regex": str,
                "monitor_keywords": lambda v: [
                    x.strip() for x in v.split(",") if x.strip()
                ],
                "monitor_regex": str,
            }

            if field not in field_handlers:
                yield event.plain_result(
                    f"❌ 字段 '{field}' 不支持批量设置（all 模式只支持频道级字段）\n"
                    "请使用 /tg set all 查看支持的字段列表"
                )
                return

            # 尝试解析值，提前发现格式错误
            raw_lower = value_str.strip().lower()
            is_clear_cmd = raw_lower in ("[]", "清空", "clear", "none", "empty", "null")
            try:
                if is_clear_cmd and field in (
                    "forward_types",
                    "filter_keywords",
                    "monitor_keywords",
                    "target_qq_sessions",
                ):
                    value_preview = []
                else:
                    value_preview = field_handlers[field](value_str)
            except Exception as e:
                field_help = self._get_single_field_help("@example", field)
                yield event.plain_result(
                    f"❌ 值格式错误：{field} = {value_str!r}\n"
                    f"错误：{str(e)}\n\n"
                    f"正确格式示例：\n{field_help}"
                )
                return

            # ─── 生成确认消息 ───
            pretty_value = value_str
            if is_clear_cmd:
                pretty_value = "清空（[]）"
            elif isinstance(value_preview, list):
                pretty_value = f"[{', '.join(str(x) for x in value_preview)}]"
            elif isinstance(value_preview, bool):
                pretty_value = "开启" if value_preview else "关闭"

            confirm_text = (
                f"⚠️ 即将**批量修改所有 {len(valid_channels)} 个监控频道** 的配置\n"
                f"字段：{field}\n"
                f"新值：{pretty_value}\n\n"
                f"请在 **30 秒内** 回复：\n"
                f"  /tg set all confirm\n"
                f"确认执行此操作。\n"
                f"（超过30秒或发送其他命令将取消）"
            )

            # 记录确认状态（使用事件发送者的ID + 时间戳）
            confirm_key = f"set_all_confirm_{event.session_id}"
            self.temp_data[confirm_key] = {
                "field": field,
                "value_str": value_str,
                "timestamp": datetime.now().timestamp(),
                "channel_count": len(valid_channels),
            }

            yield event.plain_result(confirm_text)
            return

        if len(parts) < 2:
            help_text = self.show_set_help_for_target(target)
            yield event.plain_result(help_text)
            return

        field = parts[1].strip()
        value_str = " ".join(parts[2:]).strip() if len(parts) > 2 else ""

        target_clean = target.lstrip("@").lower()
        if target_clean == "root":
            allowed_root_fields = {
                "target_qq_session",
                "target_channel",
                "phone",
                "api_id",
                "api_hash",
                "proxy",
                "debug_enabled_default",
            }

            if field not in allowed_root_fields:
                help_text = self.show_set_help_for_target(target)
                yield event.plain_result(
                    f"❌ root 模式不支持字段 '{field}'\n\n"
                    f"支持的字段如下：\n\n{help_text}"
                )
                return

            if not value_str:
                field_help = self._get_single_field_help(target, field)
                yield event.plain_result(
                    f"❌ 缺少值：root.{field} 需要提供内容\n\n格式要求：\n{field_help}"
                )
                return

            # root 字段解析逻辑
            if field == "target_qq_session":
                if value_str.lower() in ("[]", "清空", "clear", "none", "empty"):
                    value = []
                else:
                    value = self._parse_qq_targets(value_str)
            elif field == "target_channel":
                if value_str.lower() in ("[]", "清空", "clear", "none", "empty"):
                    value = []
                else:
                    value = [x.strip() for x in value_str.split(",") if x.strip()]
            elif field == "debug_enabled_default":
                normalized_value = value_str.lower()
                true_tokens = {"true", "1", "yes", "y", "开启", "开", "是"}
                false_tokens = {"false", "0", "no", "n", "关闭", "关", "否"}
                if normalized_value in true_tokens:
                    value = True
                elif normalized_value in false_tokens:
                    value = False
                else:
                    field_help = self._get_single_field_help(target, field)
                    yield event.plain_result(
                        f"❌ 值格式错误：root.{field} = {value_str!r}\n\n格式要求：\n{field_help}"
                    )
                    return
            else:
                value = value_str

            old = (
                self._get_root_qq_targets()
                if field == "target_qq_session"
                else self.config.get(field, "<未设置>")
            )
            self.config[field] = value
            self.config.save_config()

            def pp(v):
                if isinstance(v, list):
                    return "[]" if not v else ", ".join(str(x) for x in v)
                return str(v) if v not in (None, "") else "<未设置>"

            yield event.plain_result(
                f"✅ 已修改根配置 {field}\n  旧值：{pp(old)}\n  新值：{pp(value)}\n配置已保存"
            )
            plugin_name = "astrbot_plugin_telegram_forwarder"
            success, err = await self.context._star_manager.reload(plugin_name)
            msg = "\n已自动重载插件。" if success else f"\n自动重载失败：{err}"
            yield event.plain_result(msg)
            return

        # global 或 频道模式
        is_global = target_clean == "global"

        if is_global:
            cfg = self.config.get("forward_config", {})
            target_name = "全局转发配置"
            section = "forward_config"
        else:
            ch_cfg = self._find_channel_cfg(target_clean)
            if not ch_cfg:
                yield event.plain_result(f"❌ 未找到频道 @{target_clean}")
                return
            cfg = ch_cfg
            target_name = f"频道 @{ch_cfg.get('channel_username')}"
            section = "source_channels"

        field_handlers = {
            "priority": int,
            "check_interval": int,
            "msg_limit": int,
            "send_interval": int,
            "qq_merge_threshold": int,
            "batch_size_limit": int,
            "retention_period": int,
            "max_file_size": float,
            "apk_fallback_mode": str,
            "apk_direct_link_base_url": str,
            "start_time": str,
            "curfew_time": str,
            "filter_regex": str,
            "monitor_regex": str,
            "exclude_text_on_media": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "filter_spoiler_messages": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "strip_markdown_links": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "enable_deduplication": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "use_channel_title": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "ignore_global_filters": lambda v: (
                v.lower() in ("true", "1", "yes", "y", "开启", "开", "是")
            ),
            "forward_types": lambda v: [x.strip() for x in v.split(",") if x.strip()],
            "filter_keywords": lambda v: [x.strip() for x in v.split(",") if x.strip()],
            "monitor_keywords": lambda v: [
                x.strip() for x in v.split(",") if x.strip()
            ],
            "target_qq_sessions": self._parse_qq_targets,
        }

        if field not in field_handlers:
            help_text = self.show_set_help_for_target(target)
            yield event.plain_result(
                f"❌ 不支持的字段：{field}\n\n"
                f"当前目标（{target}）支持的字段如下：\n\n{help_text}"
            )
            return

        if not value_str:
            field_help = self._get_single_field_help(target, field)
            yield event.plain_result(
                f"❌ 缺少值：{field} 需要提供具体内容\n\n格式要求：\n{field_help}"
            )
            return

        handler = field_handlers[field]

        raw_lower = value_str.strip().lower()
        is_clear_cmd = raw_lower in ("[]", "清空", "clear", "none", "empty", "null")

        if is_clear_cmd and field in (
            "forward_types",
            "filter_keywords",
            "monitor_keywords",
            "target_qq_sessions",
        ):
            value = []
        else:
            try:
                value = handler(value_str)
            except (ValueError, TypeError) as e:
                field_help = self._get_single_field_help(target, field)
                error_msg = str(e)
                hint = "请检查输入格式"

                if "int" in error_msg.lower() or "float" in error_msg.lower():
                    hint = "该字段需要数字（可带小数），不要包含字母或符号"
                elif "list" in error_msg.lower():
                    hint = "列表请用英文逗号分隔，例如：文字,图片,视频"
                elif isinstance(handler, type(lambda v: True)):
                    hint = "布尔值支持：true/false/1/0/开启/关闭/是/否/开/关"

                help_text = self.show_set_help_for_target(target)
                yield event.plain_result(
                    f"❌ 值格式错误：{field} = {value_str!r}\n"
                    f"  错误：{error_msg}\n\n"
                    f"正确格式示例：\n{field_help}\n\n"
                    f"提示：{hint}\n"
                    f"可使用 /tg set {target} 查看所有字段说明"
                )
                return

        old = cfg.get(field, "<未设置>")
        cfg[field] = value

        if section == "source_channels":
            self.config[section] = self.config.get(section, [])

        self.config.save_config()

        def pretty(v):
            if isinstance(v, list):
                if not v:
                    return "[] （空）"
                return f"[{', '.join(str(x) for x in v)}]"
            if isinstance(v, bool):
                return "开启" if v else "关闭"
            if v is None or v == "":
                return "<未设置>"
            return str(v)

        yield event.plain_result(
            f"✅ 已修改 {target_name} 的 {field}\n"
            f"  旧值：{pretty(old)}\n"
            f"  新值：{pretty(value)}\n"
            "配置已保存。下次调度自动生效，也可 /tg check 立即触发。"
        )

        plugin_name = "astrbot_plugin_telegram_forwarder"
        success, err = await self.context._star_manager.reload(plugin_name)
        msg = (
            "\n已自动重载插件，变更全面生效。" if success else f"\n自动重载失败：{err}"
        )
        yield event.plain_result(msg)

    async def debug(self, event: AstrMessageEvent, action: str | None = None):
        """管理 QQ 发送诊断日志模式。

        Args:
            event: 消息事件。
            action: 子命令 (on / off / status)，为空时显示用法。
        """
        qq_sender = getattr(self.forwarder, "qq_sender", None)
        if qq_sender is None:
            yield event.plain_result("QQ 发送器未初始化。")
            return

        cmd = (action or "").strip().lower()

        if cmd == "on":
            qq_sender._debug_override["__global"] = True
            yield event.plain_result(
                "已开启 QQ 发送诊断日志（运行时覆盖，重启后失效）。\n"
                "配置页默认值可通过 /tg set root debug_enabled_default true 修改。"
            )
            return

        if cmd == "off":
            had_override = "__global" in qq_sender._debug_override
            qq_sender._debug_override.pop("__global", None)
            if had_override:
                config_default = bool(self.config.get("debug_enabled_default", False))
                if config_default:
                    yield event.plain_result(
                        "已清除运行时覆盖，当前配置页默认为开启。"
                    )
                else:
                    yield event.plain_result(
                        "已关闭 QQ 发送诊断日志（运行时覆盖已清除，回退到配置页默认：关闭）。"
                    )
            else:
                yield event.plain_result("当前没有运行时覆盖，无需清除。")
            return

        if cmd == "status":
            has_override = "__global" in qq_sender._debug_override
            config_default = bool(self.config.get("debug_enabled_default", False))
            effective = qq_sender._debug_enabled()
            if has_override:
                source = "runtime override"
            else:
                source = f"config default ({'开启' if config_default else '关闭'})"
            state = "开启" if effective else "关闭"
            yield event.plain_result(
                f"QQ 发送诊断日志：{state}（来源：{source}）"
            )
            return

        yield event.plain_result(
            "用法：/tg debug <on|off|status>\n"
            "  on      开启诊断日志（运行时覆盖，重启后失效）\n"
            "  off     关闭诊断日志（清除运行时覆盖，回退到配置页默认）\n"
            "  status  查看当前生效状态和来源"
        )

    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = (
            "/tg add <频道>       添加监控频道\n"
            "/tg rm <频道>        移除监控频道\n"
            "/tg ls               列出监控频道\n"
            "/tg check            立即检查并尝试发送\n"
            "/tg status           查看运行状态\n"
            "/tg pause            暂停抓取与发送\n"
            "/tg resume           恢复抓取与发送\n"
            "/tg queue            查看待发送队列\n"
            "/tg clearqueue [频道|all]  清空队列\n"
            "/tg get [global|频道] 查看配置\n"
            "/tg set <目标> <字段> <值>  修改配置\n"
            "/tg debug [on|off|status]  QQ 诊断日志开关\n"
            "/tg login start [手机号]\n"
            "/tg login code <验证码>（输入每位加 1 后的验证码）\n"
            "/tg login password <两步验证密码>\n"
            "/tg login status\n"
            "/tg login cancel\n"
            "/tg login reset\n"
            "/tg help"
        )
        yield event.plain_result(help_text)
