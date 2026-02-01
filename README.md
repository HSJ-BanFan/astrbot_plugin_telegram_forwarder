# Telegram 频道搬运插件


将一个公开 Telegram 频道的消息自动转发到你自己的 Telegram 频道或 QQ 群。

## 效果预览
![Preview](resources/preview.png)

## 功能特性

- **多平台转发**：支持转发到 Telegram 频道和 QQ 群（通过 NapCat/OneBot 11）。
- **全媒体支持**：
  - **图片**：自动转发。
  - **大文件/音频**：支持转发 **30MB+ 无损音频 (FLAC/WAV)** 和大文件。
  - **语音预览**：转发音频到 QQ 时，会自动生成语音条预览 + 下载链接。
  - **分片上传**：大文件会自动分片上传到图床，突破文件大小限制。
- **灵活过滤**：支持关键词过滤和正则表达式过滤。
- **客户端协议**：使用 Telethon 客户端登录，支持转发你加入的所有频道（包括受限频道）。
- **冷启动**：支持指定开始搬运的日期（如 `channel|2025-01-01`）。

## 配置说明

### 基础配置
- **enabled**: 是否启用插件。
- **phone**: **(必填)** 你的 Telegram 登录手机号（如 `+8613800000000`），首次运行需验证。
- **api_id** / **api_hash**: **(必填)** Telegram API 凭证（可使用官方公开 ID `17349` / `344583e45741c457fe1862106095a5eb`）。
- **proxy**: 代理地址，例如 `http://127.0.0.1:7897`。

### 频道配置
- **source_channels**: 源频道列表。
  - `GoogleNews`: 从最新消息开始。
  - `GoogleNews|2025-01-12`: 从 2025-01-12 的消息开始搬运。

### 目标平台配置 (支持独立开关)
- **enable_forward_to_qq**: (默认开启) 是否转发到 QQ。
- **enable_forward_to_tg**: (默认关闭) 是否转发到 Telegram 目标频道。

- **Telegram**:
  - `target_channel`: 目标频道 ID。
  - `bot_token`: 转发机器人的 Token。
- **QQ (NapCat)**:
  - `target_qq_group`: 目标 QQ 群号列表 `[123456]`。
  - `napcat_api_url`: NapCat API 地址 (例如 `http://127.0.0.1:3000/send_group_msg`)。
  - `file_hosting_url`: **(推荐)** 文件托管/图床地址。
    - 用于上传大文件和音频。
    - 支持带 AuthCode 的 URL: `https://.../upload?authCode=xxx`。
    - 若未配置，大文件将只显示文件名。

> [!NOTE]
> 如果只开启 `enable_forward_to_qq`，则不需要配置 Telegram 的 `target_channel` 和 `bot_token`。消息将直接由机器人监听并转发到 QQ。

### 过滤配置
- **filter_keywords**: 关键词黑名单列表，包含即跳过。
- **filter_regex**: 正则表达式过滤。

## 常见问题

1. **音频链接不显示？**
   - 插件会将链接和语音分两条消息发送，确保可见性。
2. **大文件发送失败？**
   - 请检查 `file_hosting_url` 是否配置正确，且图床支持分片上传（当前适配 Sanyue 图床 API）。
3. **数据存放在哪里？**
   - 插件数据（登录会话、配置）会自动迁移并存储在 `data/plugin_data/astrbot_plugin_telegram_forwarder/` 目录下，更新插件不会丢失数据。
