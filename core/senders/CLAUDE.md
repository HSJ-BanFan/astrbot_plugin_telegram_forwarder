[根目录](../../CLAUDE.md) > [core](../CLAUDE.md) > **senders**

## 模块职责
`core/senders` 是本插件负责实际将消息分发到不同目标平台（主要为 QQ 和 Telegram）的执行层。尤其针对 QQ 发送，由于目标平台协议复杂、网络不稳定、载荷体积及特殊媒体支持差异大，因此该子模块对发送链条进行了极其细致的门面模式解耦：`QQSender` 只负责编排，所有可独立的 QQ 发送细节下沉到 13 个职责单一的 `qq_*` 子模块，便于测试与演进。

## 关键子模块详细解析

### qq.py（门面）
- **核心功能**: 暴露高级 `send()` 接口对接外部 `Forwarder`。内部按"运行时探测 → 批次预处理 → 目标分发 → 结果汇总"顺序委托。
- **常量**: `DEFAULT_QQ_SEND_TIMEOUT_SEC=30.0`、`QQ_LARGE_FILE_GRACE_THRESHOLD_BYTES=10MB`、`QQ_MAX_INITIAL_SEND_TIMEOUT_SEC=300.0` 等。
- **依赖**: 几乎委托给所有 `qq_*` 子模块，自身只做编排与超时梯度回退。

### qq_media.py（媒体与路径映射层）
- **核心功能**:
  - 本地路径与目标可访问路径映射：`map_path_with_config`。
  - 文件后缀到 AstrBot 消息组件映射：`dispatch_media_file`。
- **状态机与重写细节**:
  - 当检测到大文件/视频（如 `.mp4`）时，读取 `platform_settings.path_mapping` 将插件宿主机目录映射给可能运行在不同 Docker 环境内的 QQ 实例。
  - **核心修复**: `_patch_file_to_dict` 修复了 AstrBot `File.toDict()` 输出 `file_` 而非 `file` 的 Bug，绕过 Pydantic v1 限制直接操作 `__dict__` 保证大文件正确封包。
  - **合并降级规则**: `should_merge_batch_nodes` 判断只要包含 `Record` / `File` / `Video` 这类对合并敏感的富媒体，直接绕过合并转发流退化为独立气泡。
  - `batch_contains_audio` 用于音频特殊路径判定。

### qq_circuit.py（目标熔断控制器）
- 纯净状态机。`record_target_failure` 累加 `consecutive_failures`，达阈值后开启 `open_until` 熔断期；`record_target_success` 立即清空；`target_is_open` 判定是否仍熔断。熔断期间该目标转发任务直接跳过。

### qq_dispatcher.py（分发引擎）
- 处理重试、单发送/多群分发、`big-merge`（大合并消息）的引擎。`SendProcessedBatchFn` 是发送单个批次的 `Protocol`。识别 `PROBABLE_DELIVERY_ERROR_TYPES`（如 `sendmsg_confirmation_timeout`）做快速失败。

### qq_targets.py（目标解析与异常归类）
- 纯函数模块。`dedupe_keep_order` 去重保序、`split_qq_targets` 拆分会话名与纯群号、`classify_send_error` 把 OneBot 错误归类为可重试 / 永久失败 / 熔断触发。

### qq_batch_builder.py（批次预处理）
- `ProcessedBatch` / `ProcessedBatchData` 数据结构 + `build_processed_batches()` 构造器，把原始 Telegram 消息流转换为发送层易处理的形态。

### qq_file_fallback.py（失败回退）
- `handle_file_send_failure`: 文件发送失败时的防丢失处理（如大文件降级为直链）。
- `resolve_apk_fallback_policy`: APK 特殊回退策略。

### qq_log_policy.py（日志策略）
- `QQLogPolicy`: 把 QQ 发送路径中原本散落的 `logger.info(...)` 集中管理，通过 `debug_enabled()` 回调决定是否输出诊断日志，避免正常运行时日志冗余。`/tg debug` 命令直接控制此开关。

### qq_reply_preview.py（回复预览）
- `build_reply_preview` / `build_reply_preview_from_quote` / `get_sender_display_name` / `prefetch_reply_previews` / `reply_media_label` / `REPLY_UNAVAILABLE_PREVIEW`
- QQ / OneBot 11 无 Telegram 式跨消息引用气泡，因此统一把被回复内容折叠为内联前缀（`↩ 回复 ...`）。
- 解析顺序：本批次已有消息 → `get_messages` 拉取 → `quote_text` 片段 → 占位 `[原消息不可用]` + 日志。
- 同批次内的被回复消息**不会再被跳过**（历史 bug：`existing_ids` 过滤导致 GH#22 场景只转发回复正文）。

### qq_runtime.py（平台运行时探测）
- `get_platform_instances` / `get_platform_bot` / `select_qq_platform`: 从 AstrBot 上下文发现可用 QQ 平台实例并选出最合适的平台与 bot，屏蔽平台管理器的多种实现细节差异。

### qq_send_prep.py（发送预处理）
- `resolve_qq_targets` / `normalize_qq_targets` / `resolve_send_limits` / `resolve_text_processing_options` / `flatten_batches` / `positive_int`: 把"配置 + 输入批次"整理成"可发送批次 + 目标列表 + 限额"。

### qq_send_summary.py（结果汇总）
- `build_send_summary` / `collect_processed_batch_local_files`: 构造人类可读的发送结果摘要，并收集批次引用的本地文件用于事后清理。

### qq_types.py（类型）
- `SendKind` 枚举（如普通气泡 / 合并转发 / 大合并等）与 `SendMessageFn` 等类型定义。

### telegram.py（Telegram 直发）
- `TelegramSender`: 把消息转发到配置的 `target_channel`。逻辑远简单于 QQ 路径。

## 设计决策约束 (参考 docs/superpowers/specs/)
1. **回复内容的强引用 (2026-04-16-reply-quote-full-content-design)**: QQ 原生不支持高级回复样式，采用全内容预抓取与内联前置（带 Marker node）来伪造"引用的原消息"。
2. **Debug 诊断层解耦 (2026-05-04-debug-mode-design)**: 诊断日志全由 `QQLogPolicy` 把控，不再于主体业务中散落复杂的调试判定。
3. **正确性优先 (2026-05-06-pr23-review-fixes-design)**: 处理合并失败回退时，严禁重复发送已在本地成功的分片。发送失败时的临时文件清理包裹在严格的 `try/finally` 中，且明确不能删除未完成队列所依赖的媒体。
4. **相册展示降级 (2026-05-07-qq-album-merge-preservation-design)**: 若一个 Telegram 原生相册进入 QQ `big-merge` 阶段会导致视觉撕裂，需保证 `grouped_id` 的 Album batch 退化不参与外部大合并，保持独立的图文连续展示。

## 测试与质量
- `tests/test_qq_sender.py`: `QQSender` 全链路状态测试（最深 mock）。
- `tests/test_qq_log_policy.py`: `QQLogPolicy` 开关与格式。
- `tests/test_qq_circuit.py`: `qq_circuit` 熔断状态机（`target_is_open` / `record_target_failure` / `record_target_success`）纯函数全覆盖。
- `tests/test_qq_dispatcher.py`: `qq_dispatcher` 的 `_is_probably_delivered`、`dispatch_processed_batches_to_targets`（成功 / 熔断延后 / 失败快速止损 / 疑似已送达 / 空目标跳过）、`send_processed_batch`（相册合并 / 普通发送）。
- `tests/test_qq_file_fallback.py`: `qq_file_fallback` 的模式归一化、直链构造、APK 识别、rich-media 失败判定、直链/源文件/压缩包兜底全路径。
- 大合并（big-merge）的完整分块降级树仍由 `test_qq_sender.py` 间接覆盖。

## 常见问题 (FAQ)
- **Q**: 为什么文件后缀映射、路径映射都集中在 `qq_media.py`？
  **A**: 这是 AstrBot / NapCat 跨 Docker 环境最易踩坑的点，集中化便于追踪与单元测试。
- **Q**: 新增一种"失败回退策略"应该放哪？
  **A**: 放 `qq_file_fallback.py`，并保持 `handle_file_send_failure` 的可组合性，不要侵入 `qq.py` 主流程。

## 相关文件清单
- `qq.py` — `QQSender` 门面
- `qq_batch_builder.py` — 批次预处理
- `qq_circuit.py` — 目标熔断
- `qq_dispatcher.py` — 分发引擎
- `qq_file_fallback.py` — 失败回退
- `qq_log_policy.py` — 诊断日志策略
- `qq_media.py` — 媒体与路径映射
- `qq_reply_preview.py` — 回复预览构造
- `qq_runtime.py` — 平台运行时探测
- `qq_send_prep.py` — 发送预处理
- `qq_send_summary.py` — 结果汇总
- `qq_targets.py` — 目标解析与异常归类
- `qq_types.py` — 类型定义
- `telegram.py` — `TelegramSender`

## 变更记录 (Changelog)
- **2026-07-19**: 修复回复引用（GH#22）：同批被回复消息本地构造预览；抓取失败降级 `quote_text` / `[原消息不可用]`；`exclude_text_on_media` 时仍保留引用前缀。
- **2026-07-04 (补扫)**: 新增 `qq_circuit` / `qq_dispatcher` / `qq_file_fallback` 三个独立单测文件；更新测试与质量章节，关闭既有测试缺口。
- **2026-07-04**: 补全所有 13 个子模块的职责描述（batch_builder / send_prep / send_summary / runtime / targets / reply_preview / log_policy / file_fallback / dispatcher / types）；追加测试与 FAQ 章节。
- **2026-06-08**: 补充创建 `core/senders` 文档，重点补全了 `qq_media` 的路径映射及补丁原理和 `qq_circuit` 的熔断原理。
