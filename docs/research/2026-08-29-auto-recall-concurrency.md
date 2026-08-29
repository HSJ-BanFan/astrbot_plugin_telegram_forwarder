# 🔎 Spike 调研报告: 异步撤回生命周期与协议边界探查

- **调研日期**：2026-08-29
- **调研责任人**：Research Analyst (Spike Agent)

## 1. OneBot 11 delete_msg RFC 边界
- 标准动作 `delete_msg` 参数为 `{"message_id": int}`；
- 当消息超出 2 分钟时，部分 OneBot 实现会返回 `retcode != 0`，需进行静默捕获并输出结构化警告。

## 2. Telethon 负数 Channel ID 转换陷阱
- 针对 `-100xxxx` 格式的目标频道，字符串判定必须使用 `target.isdigit() or (target.startswith("-") and target[1:].isdigit())`，防止 `-channel_name` 导致的强转异常。

## 3. 并发安全性与防死锁基准
- 基于 `asyncio.create_task` 托管倒计时协程，在 `pytest.ini` 中注入 `timeout = 15` 约束，确保无 IOCP 协程死锁。
