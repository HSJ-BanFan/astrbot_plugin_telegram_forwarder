[根目录](../../CLAUDE.md) > [core](../CLAUDE.md) > **filters**

## 模块职责
`core/filters` 是消息过滤与内容安全审核引擎。在 `Forwarder` 抓取到候选消息后、进入合并与发送队列之前，决定哪些消息应当被放行或丢弃。含三大协同组件：
- **`ContentScreeningPipeline`**：**统一双阶段内容审核流水线引擎**。
  - **INGESTION 阶段（入队前快速路径）**：评估关键词与正则表达式过滤规则（黑名单语义），命中即快速丢弃。
  - **DISPATCH 阶段（出队发送前深度审核）**：再次执行文本规则检查，并执行图片大小边界检查（超限或损坏时降级为纯文本评估）、本地二维码风险检测（`ContentSafetyFilter.check_qr`）以及 LLM 多模态安全审核（`ContentSafetyFilter.check_ai`）。
  - **周期预算与降级控制**：管理单轮 AI 审核调用额度（`ai_filter_max_calls_per_cycle`），额度耗尽自动降级为仅执行本地二维码与文本过滤；提供 `reset_cycle_budget()` 与 `reload_config(new_config)`。
  - **频道专属规则解析与缓存**：合并全局 `forward_config` 与频道 `source_channels[]` 专属过滤规则，支持 `ignore_global_filters` 覆盖，并缓存解析结果。
- **`MessageFilter`**：向后兼容门面，底层由 `ContentScreeningPipeline` 驱动，保留原 `filter_messages` 接口。
- **`ContentSafetyFilter`**：AI 内容审核（LLM 严格 JSON 判定）+ 二维码风险过滤（本地 zxing-cpp 解码 + 风险词匹配），内置 SSRF 防护的 pinned resolver。

## 入口与启动
- 由 `Forwarder` 实例化 `ContentScreeningPipeline(config)` 并在入队和发送前分别调用 `pipeline.evaluate(...)`。
- `MessageFilter(config)` 作为门面类，可在需要同步批量过滤的场景下直接调用 `filter_messages`。

## 对外接口
- **`ContentScreeningPipeline`**:
  - `evaluate(msg, channel_name="", stage=ScreeningStage.INGESTION, image_bytes=None) -> ScreeningVerdict`: 双阶段审核异步主入口。
  - `evaluate_sync(msg, channel_name="", stage=ScreeningStage.INGESTION) -> ScreeningVerdict`: 同步文本快速路径。
  - `reset_cycle_budget() -> None`: 重置本轮发送周期的 AI 审核调用额度。
  - `get_remaining_ai_budget() -> int`: 查询本周期剩余 AI 审核额度。
  - `reload_config(new_config) -> None`: 重新加载配置并清空已缓存的频道规则策略。
  - `resolve_channel_policy(channel_name="") -> ChannelScreeningPolicy`: 解析并缓存频道审核规则策略。
- **`ScreeningVerdict`**（不可变数据契约）：
  - `action: VerdictAction`: `ALLOW` 或 `DROP`。
  - `reason: str`: 诊断原因或过滤描述。
  - `stage: ScreeningStage`: 评估阶段（`INGESTION` 或 `DISPATCH`）。
  - `matched_policy: str | None`: 命中的关键词、正则规则或过滤器标识（如 `"qr_filter"`, `"ai_filter"`）。
  - `is_allowed -> bool` / `is_dropped -> bool`: 判定快捷属性。
- **`MessageFilter`**（兼容门面）：
  - `filter_messages(messages, logger_func=None) -> list[tuple[str, Message]]`: 保持原顺序过滤消息。
- **`ContentSafetyFilter`**:
  - `check_ai(text, image_bytes, config) -> dict`: 异步调 LLM 判定文本 + 图片，返回 `{"filter": bool, "msg": str}`。
  - `check_qr(image_bytes, config) -> dict`: 本地解析二维码并匹配风险词。

## 关键依赖与配置
- 依赖 `AstrBotConfig`（读取 `forward_config` 及 `source_channels`）。
- 过滤配置项：
  - `filter_keywords: list[str]`：ASCII 词使用单词边界 lookaround 避免词中误伤，非 ASCII 词大小写不敏感子串匹配。
  - `filter_regex: str` / `filter_regex_patterns: list[str]`：Python 正则，命中即丢弃。非法正则容错记 `logger.error` 并跳过。
  - `ai_filter_enabled: bool`、`ai_filter_max_calls_per_cycle: int`（默认 5）。
  - `qr_filter_enabled: bool`、`qr_filter_mode: str`、`qr_risk_keywords: list[str]`。
  - `content_filter_max_image_mb: float`：超过限制降级为纯文本评估。

## 测试与质量
- `tests/test_screening_pipeline.py`：覆盖快速文本关键词、边界检查、正则容错、双阶段隔离、二维码风险丢弃、AI 审核、预算耗尽降级、图片超限降级、频道规则继承与配置热重载。
- `tests/test_message_filter.py`：门面兼容性与历史用例回归。
- `tests/test_content_safety_filter.py`：AI 判定解析、二维码风险词命中、SSRF 防护等底层测试。

## 相关文件清单
- `__init__.py` — 导出 `ContentScreeningPipeline`, `ScreeningVerdict`, `VerdictAction`, `ScreeningStage`, `ChannelScreeningPolicy`, `MessageFilter`, `ContentSafetyFilter`
- `screening_pipeline.py` — 统一内容审核流水线引擎与不可变判定契约
- `message_filter.py` — 向后兼容门面实现
- `content_safety.py` — `ContentSafetyFilter`（AI 审核 + 二维码风险过滤，含 SSRF 防护）

## 变更记录 (Changelog)
- **2026-09-19**: Ticket #60 实现 `ContentScreeningPipeline` 统一双阶段审核流水线，定义 `ScreeningVerdict` 强类型契约，`MessageFilter` 重构为向后兼容门面，新增 `test_screening_pipeline.py`（66/66 全部通过）。
- **2026-08-15**: 新增 `ContentSafetyFilter` 文档（AI 审核 + 二维码风险过滤，`ai_filter_*` / `qr_risk_keywords` 配置、SSRF 防护、预算限量）；修正 `__init__.py` 导出声明；补充 `test_content_safety_filter.py`。
- **2026-07-04 (补扫)**: 修正接口描述（此前误记为 `should_keep` / `include_keywords` / `exclude_keywords`；实际为 `filter_messages` / `filter_keywords` / `filter_regex` 黑名单语义）；补全配置项、匹配语义与测试说明。
- **2026-07-04**: 初始化模块文档。
