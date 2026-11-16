[根目录](../../CLAUDE.md) > [core](../CLAUDE.md) > **mergers**

## 模块职责
`core/mergers` 负责把同一频道内多条 Telegram 消息合并为一个逻辑组（批次），供发送层决定是否走"合并转发（big-merge / Nodes）"或拆分为独立气泡。合并策略可插拔，由配置驱动选择。

## 入口与启动
- 由 `Forwarder.__init__` 实例化 `MessageMerger(config)`。
- `MessageMerger` 内部按配置装载一个或多个 `MergeRule` 子类实例，依次询问 `can_merge()`。

## 对外接口
- `MessageMerger.merge_messages(messages)`: 为符合规则的消息组添加分组标记，返回消息列表。
- `MessageMerger.find_defer_from_index(channel_name, messages)`: 根据消息归属决定仍需等待的队列边界。
- `MergeRule.can_merge(channel_name, msg1, msg2) -> bool`: 单条规则的可合并判定（子类实现）。

## 关键依赖与配置
- 依赖 Telethon 的 `Message` 类型。
- 子类：
  - `KeywordNextNMerge` (`keyword_next.py`): 当某条消息命中触发关键词（或正则）时，将其与后续 N 条消息（默认 2 条，受 `time_window_seconds` 默认 60s 时间窗约束）合并为一个逻辑组。配置键：`trigger_keywords` / `trigger_regex` / `next_count` / `time_window_seconds`。

## 数据模型
- `MergeRule` (base): 持有 `_positive_int` / `_normalize_keywords` 等辅助方法。
- 合并结果为 `list[list[tuple[str, Message]]]`（每个元素是 (text, message) 元组列表）。

## 测试与质量
- `tests/test_keyword_next_merge.py`: 覆盖 `KeywordNextNMerge` 的关键词触发、时间窗、计数边界等场景。

## 相关文件清单
- `__init__.py` — 导出 `MergeRule` / `KeywordNextNMerge` / `MessageMerger`
- `base.py` — `MergeRule` 抽象基类
- `merger.py` — `MessageMerger` 编排器
- `keyword_next.py` — `KeywordNextNMerge` 策略（触发词 + 后续 N 条）

## 变更记录 (Changelog)
- **2026-07-04**: 初始化模块文档；登记新增的 `KeywordNextNMerge` 合并器。
