<div align="center">

# ✈️ 电报搬运工

<i>🚛 我们只是电报的搬运工</i>

![License](https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)

</div>

## 📖 简介

一款为 [AstrBot](https://astrbot.app) 设计的功能强大的 Telegram 消息转发插件。它支持自动监控指定的公开频道，并将其中的文字、图片、音频及文件实时同步至您的 QQ 群或另一个 Telegram 频道。

---

## ✨ 功能特性

* **🌐 多平台同步**
  * 支持转发至 **QQ 群** (通过 NapCat/OneBot 11)。
  * 支持转发至 **Telegram 频道** (通过 Telethon 会话转发)。
* **📦 全媒体类型支持**
  * **图文消息**: 自动识别并保持格式同步。
  * **音频/文件**: 支持常见媒体与文件类型搬运。
  * **APK 失败降级**: QQ 拒收 `.apk/.xapk/.apkm/.apks` 时，可自动改走直链或压缩包发送。
* **🛠️ 高级控制逻辑**
  * **灵活过滤**: 内置关键词黑名单与正则表达式过滤引擎。
  * **冷启动支持**: 可指定历史日期开始搬运。在频道设置中指定 `start_time` (格式: YYYY-MM-DD) 即可。
  * **转发查重**: 自动识别频道间的转发关系，避免监控多个关联频道时出现重复消息。
  * **协议登录**: 使用 Telethon 客户端登录，支持转发您已加入的所有频道。

---

## 🚀 效果预览

![Preview](resources/img/preview.png)

---

## 指令帮助

```text
🤖 Telegram Forwarder 命令列表
─────────────
/tg add <频道>       添加监控频道
/tg rm <频道>        移除监控频道
/tg ls               列出监控频道
/tg check            立即检查并尝试发送
/tg status           查看运行状态
/tg pause            暂停抓取与发送
/tg resume           恢复抓取与发送
/tg queue            查看待发送队列
/tg clearqueue [频道|all]  清空队列
/tg get [global|频道] 查看配置
/tg set <目标> <字段> <值>  修改配置
/tg login start [手机号]                开始登录（发送验证码）
/tg login code <验证码>                 提交验证码
/tg login password <两步验证密码>       提交 2FA 密码
/tg login status                       查看登录流程状态
/tg login cancel                       取消当前登录流程
/tg login reset                        重置当前登录状态并重建客户端
/tg help             显示此帮助
```

## Web 管理页面

插件内置 Flask Web 管理页面，启动插件后默认监听：

```text
http://127.0.0.1:8180/
```

首次启动会自动生成随机 Web Token，并写入插件配置的 `web_config.token`。可在插件配置的 `web_config` 中修改 `enabled`、`host`、`port`、`token`；如需局域网访问，请显式将 `host` 改为 `0.0.0.0`。

页面支持在浏览器中修改转发配置、源频道配置、查看运行状态、清空队列，以及完成 Telegram 登录。通过 Web 页面或 `relogin.py` 本地工具提交 Telegram 验证码时，请输入 Telegram 收到的验证码原文；只有使用聊天命令 `/tg login code` 时才需要输入“每位加 1 后”的验证码。

## ⚙️ 配置说明

### 1. 账号连接
* **phone**: 您的 Telegram 登录手机号 (国际格式，如 `+86138...`)。
  - **(推荐)** 如使用`/tg login`命令登录则无需填写。
  - 如使用 `relogin.py` 生成会话文件，则此项必填。
* **api_id** / **api_hash**: **(必填)** Telegram API 凭证 (需从 [my.telegram.org](https://my.telegram.org) 获取)。

#### 申请 `api_id / api_hash` 时一直提示 `ERROR`

如果 `my.telegram.org` 创建 Telegram 应用时一直提示 `ERROR`，可以临时使用住宅 / ISP IP 访问官方页面。推荐只在浏览器里单独配置代理，不影响系统其他网络。

##### 1）获取住宅 / ISP 静态 IP

如果只是为了申请 Telegram `api_id / api_hash`，优先选择住宅 / ISP IP，不建议使用机房代理。可以根据您的偏好选择以下方式：

<details>
<summary><b>🎁 免费获取（使用原始完整配置教学）</b></summary>
<br>

##### 一、获取试用住宅 IP

* <a href="https://www.lycheeip.com/home/ip?utm_source=chatgpt.com" target="_blank">免费获取住宅 / ISP 静态 IP</a>

操作流程：

<pre>
1. 打开代理服务商网站并注册账号
2. 进入个人中心，找到自己的用户 ID
3. 联系客服，发送用户 ID，申请试用住宅 IP / 动态住宅流量
4. 客服开通后，进入代理后台生成代理线路
5. 获取代理信息
</pre>

代理后台一般会生成类似下面的格式：

<pre>hostname:port:username:password</pre>

例如：

<pre>global.example.com:10000:username-session-xxxx:password</pre>

这里需要拆成四部分使用：

<pre>
hostname  → 代理地址
port      → 端口
username  → 用户名
password  → 密码
</pre>

<img src="resources/img/proxy-trial.png" alt="代理试用开通示例" style="max-width: 100%;" />

##### 二、在 Firefox 中配置代理

打开：

<pre>
Firefox → 设置 → 常规 → 网络设置 → 设置
</pre>

选择：

<pre>
手动配置代理
</pre>

填写 `hostname` 和 `port`，并勾选“也将此代理用于 HTTPS”。

不要把整行 `global.example.com:10000:username:password` 直接填进去，应拆开填写：

<pre>
HTTP 代理：global.example.com
端口：10000
</pre>

<img src="resources/img/firefox-proxy-settings.png" alt="Firefox 代理配置" style="max-width: 100%;" />

保存后，先在 Firefox 打开：

<pre>
https://api.ipify.org
</pre>

首次访问时会弹出代理认证框，输入代理后台提供的用户名和密码。如果页面显示的 IP 已经变成代理出口 IP，说明代理配置成功。

##### 三、检查代理出口

确认 `api.ipify.org` 显示的是代理出口 IP 后，如需继续检查 IP 质量，可使用 [IPPure](https://ippure.com/?utm_source=chatgpt.com)。优先选择原生 / 住宅 IP，尽量避免机房、Cloud、VPS 以及 AWS / Azure / Google Cloud / Leaseweb 等机房 ASN。

<img src="resources/img/ippure-check-result.png" alt="IPPure 检测结果" style="max-width: 100%;" />

</details>

<details>
<summary><b>💰 付费获取（直接订阅链接）</b></summary>
<br>

* <a href="https://mitce.net/aff.php?aff=41410" target="_blank">订阅台湾 / 香港静态 IP</a>（约每月 3 元 / 100GB，以页面实际信息为准）

**使用方法：**

该付费链接提供的是代理软件订阅（机场订阅）。购买后复制订阅链接，直接导入您的代理客户端软件（如 Clash、v2rayN、Shadowrocket 等），开启系统代理或配合浏览器代理插件使用即可，无需进行任何繁琐的浏览器手动配置。

</details>

* **proxy**: 代理地址，例如 `http://127.0.0.1:7890`。
* **telegram_session**: 
  - **(推荐)** 您可使用`/tg login`命令登录 Telegram 账号，登录成功后会自动生成会话文件并保存到数据目录，无需手动配置此项。
  - 也可在本地使用 `relogin.py` 生成 `.session` 文件并在此处上传，以绕过 Docker 环境下的验证码输入问题。

### 2. 获取登录 Session
#### 1）使用内置登录命令（推荐）
1. 发送 `/tg login start <手机号>` 命令，随后后会收到 Telegram 验证码。
2. 发送 `/tg login code <验证码>` 命令，根据提示输入验证码（为了增强安全性，请将收到的验证码每位数字加一后输入，如接收到验证码`25691`则输入`36702`），完成登录流程。
#### 2）使用本地工具生成 Session
由于 Docker/后台环境无法直接输入验证码，或因服务器网络环境触发人机验证（Cloudflare 等）导致登录失败，请按以下步骤在本地环境中生成会话文件：
1. 进入插件目录：`cd data/plugins/astrbot_plugin_telegram_forwarder`
2. 运行登录工具：`python relogin.py` (请确保已安装依赖)
3. 按提示输入手机号与 Telegram 收到的验证码原文（不要每位加 1），生成的 `user_session.session` 会自动保存至数据目录。
4. 重启 AstrBot 即可生效。

> **提示**：如果机器人的网络环境被 Telegram 要求人机验证而无法登录，您可以将此工具下载到本地电脑，更换网络环境运行成功后再将生成的 `.session` 文件上传至插件数据目录。

### 3. 目标平台配置
* **QQ 配置**:
  * `target_qq_session`: 接收消息的 QQ 目标会话列表（支持群号，或完整会话名，如 `平台ID:GroupMessage:群号` / `平台ID:FriendMessage:用户ID`）。
* **Telegram 配置**:
  * `target_channel`: 接收消息的目标频道 ID。

### 4. 源频道配置
您可以为每个频道进行精细化设置：
* **channel_username**: 频道用户名 (不带 @)。
* **target_qq_sessions**: 填写则覆盖全局 QQ 目标会话（支持群号或完整会话名），留空使用全局配置。
* **start_time**: 起始日期 (YYYY-MM-DD)。留空则仅转发新消息。
* **check_interval**: 专属检测间隔。为 0 时使用全局配置。
* **priority**: 转发优先级。数值越大优先级越高。未设置或为 0 时优先级最低。高优先级频道的消息将优先于低优先级频道发送。
* **forward_types**: 选择需要搬运的类型 (文字/图片/视频/音频/文件)。
* **max_file_size**: 单个文件大小限制 (MB)，0 表示不限制。
* **ignore_global_filters**: 开启后，该频道将**忽略全局**的 filter_keywords 和 filter_regex（但仍执行本频道自己的过滤规则）。常用于白名单式频道或重要通知频道。
* **filter_spoiler_messages**: 是否过滤遮罩/剧透消息（支持继承全局配置）。
* **monitor_keywords**: 监听关键词。命中后会立即触发转发。
* **monitor_regex**: 监听正则。命中后会立即触发转发。

### 5. 全局转发配置
* **qq_merge_threshold**: QQ 大合并：本次要发的消息数 >= 此值时，全部打包成一条合并转发消息。设为 ≤1 则永不触发大合并。推荐 5~10
* **qq_big_merge_mode**: QQ 大合并的聚合范围：『独立频道』：每个频道独立判断是否合并（推荐）；『混合所有频道』：所有频道消息总数达标才合并成一条超大转发；『关闭』强制不使用大合并
* **use_channel_title**: 是否在消息头部显示频道名称。
* **enable_deduplication**: 是否启用转发查重。开启后，如果频道 A 转发了频道 B 的消息，且频道 B 也在监控列表中，则频道 A 的这条转发消息将被自动跳过。
* **exclude_text_on_media**: 开启后，包含媒体的消息将不再发送文本内容（包含 From 头部）。
* **apk_fallback_mode**: 当 QQ 发送 `.apk/.xapk/.apkm/.apks` 返回 `rich media transfer failed` 时的降级策略：`关闭` / `直链` / `压缩包` / `直链优先，失败转压缩包`
* **apk_direct_link_base_url**: 直链降级使用的公网下载基地址，例如 `https://files.example.com/downloads/`。请确保外部可访问，且生成的文件 URL 可被 QQ 客户端打开。
* **filter_spoiler_messages**: 过滤 Telegram 遮罩/剧透消息（文本剧透实体与媒体剧透标记）。
* **strip_markdown_links**: 开启后，[文本](链接) 只保留「文本」，链接部分被完全丢弃
* **batch_size_limit**: 每次转发执行时，单次处理的消息批次上限。
* **send_interval**: 轮询待发送队列并执行转发任务的周期。
* **retention_period**: 消息在队列中的最大保留时间，过期将自动丢弃。
* **curfew_time**: 宵禁时间段 (格式：`11:11-14:12`)。在此时间段内，插件将停止抓取新消息和转发任务。支持跨天（如 `23:00-07:00`）。留空则禁用。
* **monitor_keywords**: 全局监听关键词。与频道监听关键词做并集，命中后立即触发转发。
* **monitor_regex**: 全局监听正则。与频道监听正则共同生效，命中后立即触发转发。

### 6. 跨环境部署：路径映射（AstrBot 与 NapCat 部署方式不同时必读）

当 AstrBot 与 NapCat **不在同一个文件系统**中运行时（例如 AstrBot 跑在宿主机、NapCat 跑在 Docker 容器里，或两者在不同容器中），**必须配置路径映射**，否则文件、视频以及语音发送失败后的源文件补发都会失败。

**典型报错**（NapCat 侧收到宿主机路径，容器内不存在）：

```text
ActionFailed retcode=1200, message="ENOENT: no such file or directory,
copyfile '/E:/.../plugin_data/astrbot_plugin_telegram_forwarder/xxx.flac' -> '/app/.config/QQ/NapCat/temp/....flac'"
```

**原因**：图片和语音条会在 AstrBot 进程内转成 base64 传输，不受影响；但文件（File）、视频（Video）等大载荷是**按本地路径**下发给 NapCat 的。NapCat 在容器内无法访问 AstrBot 宿主机上的路径，就会报 `ENOENT`。

**解决步骤**：

1. **把插件数据目录挂载进 NapCat 容器**（只读即可）。以 docker run 为例：

   ```bash
   -v /path/to/astrbot/data/plugin_data:/plugin_data:ro
   ```

   docker-compose 写法：

   ```yaml
   volumes:
     - /path/to/astrbot/data/plugin_data:/plugin_data:ro
   ```

2. **在 AstrBot 中配置路径映射**：打开 AstrBot WebUI → 配置 → 其他配置 → 平台设置 → `路径映射 (path_mapping)`，添加一条规则，格式为 `<AstrBot 侧路径>:<NapCat 容器内路径>`：

   ```text
   /path/to/astrbot/data/plugin_data:/plugin_data
   ```

   Windows 宿主机同样支持盘符路径（建议使用正斜杠）：

   ```text
   E:/astrbot/data/plugin_data:/plugin_data
   ```

3. **重启 AstrBot** 使配置生效。

配置成功后，日志中会出现类似 `[QQSender] Path mapping: 'E:\\...\\xxx.flac' -> '/plugin_data/astrbot_plugin_telegram_forwarder/xxx.flac'` 的映射记录。

> **提示**：如果 AstrBot 与 NapCat 运行在同一环境（同宿主机直装，或同一容器内共享文件系统），无需此配置。

---

## 💡 常见问题

* **Q: 音频链接不显示？**
  * **A**: 插件会将外链和语音分两条消息发送，请检查消息是否被群管屏蔽。
* **Q: 大文件发送失败？**
  * **A**: 请先确认 `forward_types` 和 `max_file_size` 配置，以及目标平台本身的消息限制。QQ 发送会按本地文件大小自动延长等待时间；超时不会自动重复发送，以避免重复消息。
* **Q: 文件/音频发送报 `ENOENT: no such file or directory, copyfile ...`？**
  * **A**: 这是 AstrBot 与 NapCat 部署环境不同（如 NapCat 跑在 Docker 中）导致 NapCat 无法访问 AstrBot 侧的文件路径。请参阅上文「[配置说明 → 6. 跨环境部署：路径映射](#6-跨环境部署路径映射astrbot-与-napcat-部署方式不同时必读)」完成目录挂载与 `path_mapping` 配置。
* **Q: 数据存放在哪里？**
  * **A**: 所有登录会话与配置均持久化在 `data/plugin_data/astrbot_plugin_telegram_forwarder/` 目录下，更新插件不会丢失。

---

## ❤️ 支持

* [AstrBot 帮助文档](https://astrbot.app)
* 如果您在使用中遇到问题，欢迎提交 [Issue](https://github.com/HSJ-BanFan/astrbot_plugin_telegram_forwarder)。

---

<div align="center">

**觉得好用的话，给个 ⭐ Star 吧！**

</div>
