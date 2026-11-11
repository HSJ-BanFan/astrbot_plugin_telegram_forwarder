---
name: astrbot-conventions
description: "本仓库（AstrBot Telegram→QQ/TG 转发插件）的硬性工程约定：功能提交必须带对应单测，影响既有调用方或模块职责的改动必须先写 ADR。改代码、动接口、提 PR 前加载。"
---

# AstrBot 转发插件：工程约定

两条硬约束 + 一条验收前提。不满足就**不要提 PR**——先在工单线程里问一个问题，把选项摊开。

## 1. 功能提交必须带对应单测

- 位置 `tests/`，命名 `test_<被测模块>.py`；用例名说清行为，不要断言私有实现细节。
- 复用 `tests/conftest.py` 的 fixture：它已经把 `telethon` 与 AstrBot 内部模块 mock 掉，不要自己再搭一层网络桩。
- 至少覆盖两条：本次改动的正常路径，以及它要防住的回归/边界。

跑法（**首选 `verify.sh`，不要自己猜该用哪个 Python**）：

```bash
bash verify.sh                                       # 仓库自己的门：自选装了 pytest 的解释器，再 pytest -q
python -m pytest -q tests/test_<module>.py           # 只跑相关文件
pytest tests/ -v --cov=. --cov-report=term-missing   # 全量 + 覆盖率
```

`verify.sh` 的退出码要分清：**0** 通过；**1** 测试跑了但有失败；**2** 环境里找不到装着 pytest 的 Python——这是**环境问题，不是代码不合格**，不要把它报成「测试失败」。缺依赖时先 `pip install -r requirements.txt pytest pytest-cov`（本仓库没有 `requirements-dev.txt`）。

豁免：纯文档、纯注释、纯资源替换，且 `tests/` 里不存在对应被测行为。豁免要在 PR 描述里写明理由。

## 2. 影响既有调用方 → 先写 ADR，再动手

命中任一条就必须先写 ADR：

- 改变现有函数/方法的公共签名、返回结构或抛出类型；
- 改模块职责（例如把发送逻辑搬出 `core/senders/`，或让 `Forwarder` 承担新的编排职责）；
- 需要多个独立模块协同重设计（`core/` ↔ `web/` ↔ `core/senders/` 同时改协议）；
- 改变 `_conf_schema.json` 的字段语义或默认值（直接影响已部署实例）；
- 动 `core/recall.py` 的撤回生命周期（已有两份 ADR 约束它）。

写法：`docs/adr/ADR-00N-<kebab-slug>.md`，沿用现有两份的三段式：

1. `## 1. 背景与业务痛点 (Context)`
2. `## 2. 决策方案 (Decision)` —— 必须含**被否掉的备选与否决理由**
3. `## 3. 后果与收益 (Consequences)` —— 正面收益、潜在约束、迁移成本

写不出「备选方案与否决理由」= 决策还没定 → **停止实现**，问一个问题。不要自己挑方案，也不要把半成品 draft PR 当答案。

## 3. 验收前提：三条不变式

任何改动都要守住 `docs/agents/domain.md` 里的三条：**零消息泄露**（任何失败路径都要有 fail-safe）、**时钟与超时保护**（异步撤回与网络请求默认 ≤15 s）、**严格类型标注**（Python 3.10+）。它们是验收条件，不是建议。

## 不适用

纯文案、依赖升级、只改 `.md` —— 按常规流程走，不必加载本技能。
