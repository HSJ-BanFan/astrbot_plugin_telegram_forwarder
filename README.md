# Telegram 频道搬运插件

将一个公开 Telegram 频道的消息自动转发到你自己的 Telegram 频道或 QQ 群。

## 功能特性

- **多平台转发**：支持转发到 Telegram 频道和 QQ 群（通过 NapCat）。
- **图片支持**：自动抓取并转发消息中的图片。
- **无需账号**：使用公开预览链接抓取，无需登录 Telegram 账号（降低封号风险）。
- **防重复**：基于消息 ID 去重，避免重复发送。

## 配置说明

### 基础配置
- **enabled**: 是否启用插件。
- **check_interval**: 检查更新的频率（秒），建议 60 秒以上。
- **phone**: **(必填)** 你的 Telegram 登录手机号（国际格式，如 `+8613800000000`）。首次运行时需在控制台输入验证码。
- **api_id**: **(必填)** App API ID。
  - 可自己申请（my.telegram.org）。
  - 也可使用 Telegram Desktop 官方公开 ID: `17349`
- **api_hash**: **(必填)** App API Hash。
  - 官方公开 Hash: `344583e45741c457fe1862106095a5eb`
- **proxy**: 代理地址，例如 `http://127.0.0.1:7897`。

### 频道配置
- **源频道用户名列表**: 需要监听的频道（支持 频道用户名 或 频道链接）。
  - 格式：`GoogleNews` 或 `GoogleNews|2025-01-12` (指定起始日期)。
  - **支持受限频道**：因为是客户端登录，所有你账号能看到的频道（包括屏蔽了 Web 预览的敏感频道）都可以搬运！

### Telegram 转发配置
- **bot_token**: 你的 Telegram Bot Token（从 @BotFather 获取）。
- **target_channel**: 接收消息的目标频道 ID（例如 `@my_channel` 或 `-100xxxxxxx`）。

### QQ 转发配置
- **target_qq_group**: 接收消息的 QQ 群号。
- **napcat_api_url**: NapCat 的 API 地址，通常为 `http://127.0.0.1:3000/send_group_msg`。

## 常见问题

1. **为什么没消息？**
   - 检查 `source_channel` 是否正确，能在浏览器打开 `https://t.me/s/{source_channel}` 说明是公开的。
   - 检查代理配置是否连通。
   - 查看 AstrBot 后台日志是否有报错。

2. **QQ 转发失败？**
   - 确保 NapCat 运行正常，且 `napcat_api_url` 地址正确。
   - 检查 Bot 是否在目标群里且未被禁言。
