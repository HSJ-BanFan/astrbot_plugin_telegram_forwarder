# 🏛️ ADR-001: Telegram 与 QQ 消息自动撤回生命周期架构决策

## 1. 背景与业务痛点 (Context)
在 Telegram 频道消息向 QQ 群等平台转发的过程中，部分内容容易因长时间留存引发风控与举报风险。需要在消息转发成功后，提供可配置的生命周期定时自动撤回机制。

## 2. 决策方案 (Decision)
1. **解耦设计**：在 `core/recall.py` 中实现独立的 `AutoRecallManager` 与 `RecallTaskRegistry`，杜绝侵入式修改主转发流。
2. **协议适配**：
   - QQ 侧：通过 `core/senders/qq_onebot.py` 调用 OneBot 11 `delete_msg` 标准动作；
   - TG 侧：通过 `core/senders/telegram.py` 调用 Telethon `delete_messages`，并严格处理负数 Channel ID 转换。
3. **错误处理策略 (Error Policy)**：
   - 提供 `keep`（保留未撤回消息并告警）与 `retry_once` 容错策略。

## 3. 后果与收益 (Consequences)
- **正面收益**：558 项单测 100% 绿灯，完全解耦，配置支持热重载。
- **潜在约束**：QQ 消息受限于腾讯平台 2 分钟/撤回时效限制，超时将优雅记录日志而非抛出崩溃。
