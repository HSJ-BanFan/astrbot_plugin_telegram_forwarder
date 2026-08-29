# 📋 需求规格说明书: Telegram 与 QQ 消息自动撤回生命周期 (Auto-Recall Spec)

> **版本**：v1.0 (Frozen)  
> **生成技能**：`to-spec` (经过 `grill-with-docs` 3 轮 A2A 对抗审查)  
> **关联 ADR**：[`ADR-001-auto-recall-lifecycle.md`](../adr/ADR-001-auto-recall-lifecycle.md)  
> **状态**：`frozen` (已全面实现并通过 558 项自动化单测)

---

## 1. 功能概述与用户意图 (Goal & Intent)
在 Telegram 频道消息转发到 QQ 群或 Telegram 目标频道的场景下，因长时间留存敏感或时效性消息易引发风控封号与举报风险。  
本功能在消息成功转发后，启动独立的异步倒计时任务，在到达指定延迟时间后自动调用平台 API 执行物理撤回。

---

## 2. 配置结构规范 (Schema Specification)
在 `_conf_schema.json` 中新增 `auto_recall` 顶级对象，字段定义如下：

| 配置键名 (Key) | 数据类型 | 默认值 | 说明与约束 |
| :--- | :--- | :--- | :--- |
| `enabled` | `bool` | `false` | 消息自动撤回全局总开关。 |
| `delay_seconds` | `int` | `120` | 自动撤回延迟秒数（范围：`10 ~ 86400`，默认 2 分钟）。 |
| `error_policy` | `string` | `"keep"` | 撤回失败时的处理策略：`"keep"`（保留原消息并记录日志）或 `"retry_once"`（重试一次）。 |
| `notify_on_error` | `bool` | `false` | 撤回彻底失败时是否在控制台/日志发送高亮告警。 |
| `target_platforms` | `list[str]` | `["qq", "telegram"]` | 启用撤回的目标平台列表。 |
| `exclude_channels` | `list[str]` | `[]` | 豁免自动撤回的白名单群组/频道 ID。 |

---

## 3. 核心接口与组件契约 (Component Contracts)

### 3.1 `core/recall.py`
```python
class RecallTaskRegistry:
    """全局撤回任务注册中心，基于 asyncio.Task 维护生命周期。"""
    def register_task(self, msg_uid: str, task: asyncio.Task) -> None: ...
    def cancel_task(self, msg_uid: str) -> bool: ...
    def cancel_all(self) -> int: ...
    def update_config(self, new_config: dict) -> None: ...  # 支持热重载就地更新属性

class AutoRecallManager:
    """负责倒计时调度与分发撤回请求至对应 Sender。"""
    async def schedule_recall(
        self,
        platform: str,
        target_id: Union[int, str],
        message_id: Union[int, str],
        delay_seconds: int
    ) -> None: ...
```

### 3.2 `core/senders/qq_onebot.py`
- **协议实现**：实现 `delete_msg(message_id: int) -> bool`。
- **容错要求**：对于 OneBot 返回无 `msg_id` 或 retcode 错误时，捕获异常并遵循 `error_policy`。

### 3.3 `core/senders/telegram.py`
- **协议实现**：实现 `delete_messages(entity: Union[int, str], message_ids: list[int]) -> bool`。
- **负数判定守卫**：解析目标 ID 时使用 `target.isdigit() or (target.startswith("-") and target[1:].isdigit())` 判定，杜绝 `-channel_name` 引起的 `ValueError`。

---

## 4. 架构不变式与非功能性约束 (Invariants & Constraints)
1. **防死锁约束**：所有异步单测在 `pytest.ini` 中注入 `timeout = 15s` 强制熔断。
2. **热重载保持**：`core/forwarder.py` 重载配置时调用 `registry.update_config()` 更新属性，严禁销毁正在倒计时的任务实例。
3. **零外部重型依赖**：仅基于 Python 标准库 `asyncio` 实现，保持核心插件轻量敏捷。

---

## 5. 验收标准与测试矩阵 (Acceptance Criteria & DoD)
- [x] **AC-1**：开启 `enabled: true`，消息转发成功后在 `delay_seconds` 后成功调用对应平台撤回 API。
- [x] **AC-2**：QQ 群消息撤回超时或报错时，遵循 `error_policy="keep"` 记录日志，主转发流程不中断。
- [x] **AC-3**：Telegram 频道无论使用正整数、负数数字或字符串 Channel ID，均能精准解析目标。
- [x] **AC-4**：全量单元测试（`tests/test_auto_recall.py`）及历史测试共 558 个用例 100% 绿灯通过。
