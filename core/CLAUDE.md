[根目录](../CLAUDE.md) > **core**

## 模块职责
`core` 模块是本插件的核心业务层，主要负责：
- 管理与 Telegram 的持久化连接会话（`TelegramClientWrapper`），含跨插件重载的 `sys` 级客户端缓存与授权缓存。
- 执行周期性抓取、消息优先级调度、去重及分发的逻辑中枢（`Forwarder`）。
- 监听与解析所有来自框架层的 `/tg` 配置命令及认证交互（`PluginCommands`）。
- 管理媒体文件的下载策略与大小限制过滤（`MediaDownloader`）。
- 提供 Web 管理后端服务（`WebAdminServer`，Flask，默认 `127.0.0.1:8180`）。
- 为 Web 选择器提供带 TTL 的平台列表缓存（`QQGroupCache` / `TGChannelCache`）。
- 对各个平台的细分发送进行解耦支持（如 `senders`、`filters`、`mergers` 子模块）。

## 入口与启动
- **运行时入口**: `forwarder.py` / `Forwarder` 类。由根目录的 `main.py` 实例化，并在 APScheduler 中以双 job（`check_updates` / `send_pending_messages`）周期性拉起。
- **命令入口**: `commands.py` / `PluginCommands` 类。处理 `/tg` 命令组：`add` / `rm` / `ls` / `check` / `status` / `pause` / `resume` / `queue` / `clearqueue` / `get` / `set` / `login` / `debug` / `help`。
- **Web 后端入口**: `web_admin.py` / `WebAdminServer` 类。由 `main.py:Main._start_web_admin_server()` 在 `initialize()` 阶段启动，独立 Flask 线程。

## 对外接口
- `Forwarder` 对外暴露 `check_updates()` / `send_pending_messages()` / `stop()` / `shutdown(timeout)` / `qq_sender`（运行时延迟引导）等。
- `Main._register_dashboard_web_apis()` 在插件根注册 25 条 Web API（24 条 legacy `/api/...` 路由 + `page/dashboard` 聚合端点），转发至 `WebAdminServer` 方法。

## 关键依赖与配置
- 严重依赖外层传入的 `AstrBotConfig`（进行合并覆盖式的配置解析）。
- 核心依赖 `Telethon` 客户端，以及用于保存状态的 `Storage` 组件（位于 `common/`）。
- `WebAdminServer` 依赖 `telethon.errors` 中的登录异常类型与 `StringSession`，并通过 `QQGroupCache` / `TGChannelCache` 桥接平台探测。

## 数据模型
- `Forwarder.stats`: 字典，记录 `forward_success` / `forward_failed` / `forward_attempts` / `acked_messages` / `failed_messages` / `deferred_messages` / `last_reset`。
- `QQGroupCache` / `TGChannelCache`: 90s TTL，记录 `groups` / `channels` 列表、`available` 标志、人类可读 `message`。
- Web 登录流程的临时状态由 `WebAdminServer` 内部维护（`_login_*` 字段，受锁保护）。

## 核心设计模式
- **锁机制**: `Forwarder` 采用 `_global_send_lock` 与 `_send_dispatch_lock` / `_channel_locks` 防止不同频道、不同批次在重叠调度期间产生交错冲突。
- **异步生成器**: 控制指令全盘采用异步生成器与框架层交互。
- **配置合并**: 采用策略树解析全局 `forward_config` 与具体频道自定义配置的合并。
- **跨重载缓存**: `TelegramClientWrapper` 把客户端句柄挂在 `sys._telegram_forwarder_client_cache`，避免插件保存配置时被重载误伤。
- **Web 路由双层注册**: 同一 handler 同时挂到 `/api/...`（legacy）与 `/page/dashboard`（聚合），由 `WebAdminServer` 统一调度，便于前端在独立部署与插件沙箱两种环境下复用。

## 测试与质量
- `tests/test_forwarder_send_pending.py` 覆盖调度主流程。
- `tests/test_commands_debug.py` 覆盖 `/tg debug` 开关。
- `tests/test_client_session_schema.py` + `tests/test_relogin.py` 覆盖 Telethon 会话兼容与重登。
- `tests/test_web_admin.py` 覆盖 Web 管理后端的关键路径。

## 常见问题 (FAQ)
- **Q**: 配置保存后插件被重载，Telegram 客户端会断吗？
  **A**: 不会。`TelegramClientWrapper` 使用 `sys` 级缓存，跨重载复用同一个客户端实例；会话文件通过 `user_session.session` 持久化。
- **Q**: Web 管理页面打不开？
  **A**: 检查 `web.enabled` 是否为 `true`、端口 `8180` 是否被占用、`web.token` 是否为弱默认值（`123456` 会被警告）。
- **Q**: `MediaDownloader` 的大小限制和临时文件清理是怎样的？
  **A**: 大小限制 `max_size_mb` **只对非图片媒体生效**（图片始终下载）；动画贴纸（`.tgs`）与自定义动图表情（`DocumentAttributeAnimated` / `DocumentAttributeCustomEmoji`）会被跳过（QQ 无法显示）。下载失败自动重试最多 3 次，期间若客户端断开会尝试 `connect()` 重连；`asyncio.CancelledError` 原样上抛（绝不吞掉）。下载器**不负责**临时文件清理——下载产出的本地路径会挂到批次的 `local_files`，由 `qq_send_summary.collect_processed_batch_local_files()` 汇总，最终在发送成功后由 `Forwarder` 清理。

## 相关文件清单
- `forwarder.py` — 抓取与发送编排
- `commands.py` — `/tg` 命令组
- `client.py` — `TelegramClientWrapper`
- `downloader.py` — `MediaDownloader`
- `web_admin.py` — `WebAdminServer`（Flask 后端 + 登录流程 + 配置导入导出）
- `qq_group_cache.py` — `QQGroupCache`（带 TTL 的 QQ 群列表缓存）
- `tg_channel_cache.py` — `TGChannelCache`（带 TTL 的 Telegram 频道列表缓存）
- 子模块：`senders/`、`mergers/`、`filters/`（各有独立 CLAUDE.md）

## 变更记录 (Changelog)
- **2026-07-04**: 新增 `web_admin.py` / `qq_group_cache.py` / `tg_channel_cache.py` 的职责描述；补全对外接口、数据模型、测试覆盖与 FAQ；引入子模块文档链接。
- **2026-06-08**: 初始化模块级自适应文档。
