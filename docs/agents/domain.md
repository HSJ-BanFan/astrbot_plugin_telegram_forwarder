# 📖 业务领域模型规范 (Domain Model)

## 1. 核心业务实体与通用语言 (Ubiquitous Language)
- **`Forwarder`**: 核心消息转发中枢，负责在 Telegram 与 QQ / Telegram 目标群组/频道之间建立双向管道。
- **`AutoRecall`**: 防举报与消息生命周期防护子系统，负责在消息成功发送后按配置倒计时自动执行撤回。
- **`RecallRegistry`**: 撤回任务全局注册中心，基于 `asyncio.Task` 队列管理生命周期，支持优雅取消与热重载。
- **`Sender Protocol`**: 发送器抽象协议层，目前支持 `QQOneBotSender` 与 `TelegramSender`。

## 2. 核心架构不变式 (Invariants)
1. **零消息泄露**：无论发送失败、撤回超时还是网络异常，必须触发 Fail-Safe 保底机制。
2. **时钟与超时保护**：所有异步撤回与网络请求必须具备防御性超时（默认不超过 15s），防止协程死锁。
3. **类型守卫**：全链路采用 Python 3.10+ Strict Type Hints，杜绝未捕获的运行时类型异常。
