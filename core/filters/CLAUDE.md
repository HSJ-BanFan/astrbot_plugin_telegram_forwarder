[根目录](../../CLAUDE.md) > [core](../CLAUDE.md) > **filters**

## 模块职责
`core/filters` 是消息过滤引擎。在 `Forwarder` 抓取到候选消息后、进入合并与发送队列之前，决定哪些消息应当被丢弃。含两套互补机制：
- **`MessageFilter`**：**黑名单语义**，命中规则的消息被丢弃，其余原样保留。
- **`ContentSafetyFilter`**：**AI 内容审核 + 二维码风险过滤**（NSFW / 色情 / 网贷 / 博彩 / 诈骗类二维码等），由 LLM 判定并在每周期限量调用。

## 入口与启动
- 由 `Forwarder.__init__` 实例化 `MessageFilter(config)` 并在抓取流程中调用。
- 不持有独立的调度入口，纯协作式组件。

## 对外接口
- `MessageFilter.filter_messages(messages, logger_func=None) -> list[tuple[str, Message]]`
  - 入参：`(channel_name, Message)` 元组列表 + 可选日志回调。
  - 返回：通过过滤的消息子集（保持原顺序，不修改入参对象）。
  - 短路：若 `forward_config` 中既无 `filter_keywords` 也无 `filter_regex`，直接原样返回，不做任何遍历。
  - 命中日志：丢弃时调用 `logger_func(f"[Filter] Filtered by ...")`（若提供）。
- `ContentSafetyFilter.check_ai(text, image_bytes, config) -> dict`: 调 LLM 判定文本 + 图片，返回 `{"filter": bool, "msg": str}`。对文本构造 search 文本、解码图片走 `check_qr()`。
- `ContentSafetyFilter.check_qr(image_bytes, config) -> dict`: 本地解析二维码，命中 `qr_risk_keywords` 风险词即过滤；解码失败按图片非风险处理。

## 关键依赖与配置
- 依赖 `AstrBotConfig`（读取 `forward_config` 子表）与 Telethon 的 `Message` 类型（仅用 `.text` 字段）。
- `MessageFilter` 配置项（均位于 `forward_config` 下；每频道 `source_channels[]` 可用同名字段覆盖，由 `Forwarder` 合并配置时决定）：
  - `filter_keywords: list[str]`：命中任一即丢弃。匹配方式为大小写不敏感子串（`keyword.lower() in msg.text.lower()`）。
  - `filter_regex: str`：Python 正则，`re.search(filter_regex, msg.text)` 命中即丢弃，**区分大小写**。正则非法时记 `logger.error` 并跳过该规则（不抛出，保证抓取主循环不中断）。
- `ContentSafetyFilter` 配置项：
  - `ai_filter_enabled: bool`（默认关闭）：总开关。
  - `ai_filter_base_url` / `ai_filter_api_key` / `ai_filter_model` / `ai_filter_prompt` / `ai_filter_timeout`：OpenAI 兼容 LLM 端点与提示词（`DEFAULT_AI_FILTER_PROMPT` 兜底）。
  - `ai_filter_allow_private_endpoint: bool`：是否放行 localhost / 内网 base_url（默认拒绝，防 SSRF；放行时用 `_PinnedResolver` 锁定 DNS）。
  - `ai_filter_max_calls_per_cycle: int`（默认 5）：每个 send 周期内 LLM 调用预算，超出后自动跳过 AI 判定但保留 QR 本地过滤。
  - `qr_risk_keywords: list[str]`：`DEFAULT_QR_RISK_KEYWORDS`（loan/借款/贷款/网贷/博彩/色情等）兜底。
  - `content_filter_max_image_mb: float`：参与过滤的最大图片体积（过大跳过，防内存峰值）。
- LLM 判定需解析为严格 JSON `{"filter": bool, "msg": str}`（`parse_ai_decision`），非法输出按不过滤处理。

## 数据模型
- 无独立持久化状态，纯内存计算。

## 测试与质量
- `tests/test_message_filter.py`：覆盖空配置短路、关键词命中丢弃、正则命中丢弃、非法正则容错、`logger_func` 回调、大小写不敏感等。
- `tests/test_content_safety_filter.py`：覆盖 AI 判定解析、二维码风险词命中、预算限量、非法 AI 输出兜底、SSRF 防护等。
- 另由 `tests/test_forwarder_send_pending.py` 间接覆盖集成路径。

## 相关文件清单
- `__init__.py` — 导出 `MessageFilter` / `ContentSafetyFilter`
- `message_filter.py` — `MessageFilter` 黑名单过滤实现
- `content_safety.py` — `ContentSafetyFilter`（AI 审核 + 二维码风险过滤，含 SSRF 防护的 pinned resolver）

## 变更记录 (Changelog)
- **2026-08-15**: 新增 `ContentSafetyFilter` 文档（AI 审核 + 二维码风险过滤，`ai_filter_*` / `qr_risk_keywords` 配置、SSRF 防护、预算限量）；修正 `__init__.py` 导出声明；补充 `test_content_safety_filter.py`。
- **2026-07-04 (补扫)**: 修正接口描述（此前误记为 `should_keep` / `include_keywords` / `exclude_keywords`；实际为 `filter_messages` / `filter_keywords` / `filter_regex` 黑名单语义）；补全配置项、匹配语义与测试说明。
- **2026-07-04**: 初始化模块文档。
