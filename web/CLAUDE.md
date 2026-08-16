[根目录](../CLAUDE.md) > **web**

## 模块职责
`web/` 是 Web 管理控制台的 **唯一手改前端源**。它是一个无构建步骤的原生前端（HTML + 模块化 CSS + ES module JS），既能直接被 `core/web_admin.py` 的 Flask 服务挂到 `http://127.0.0.1:8180/` 提供服务，也能经 `scripts/build_frontend.py` 编译为 `pages/dashboard/` 沙箱产物嵌入 AstrBot Dashboard 插件页。

## 入口与启动
- **HTML 入口**: `index.html` — 包含认证屏（`#authScreen`）与应用外壳（`#appShell` + 侧边栏导航）。
- **JS 入口**: `assets/app.js` — 导入各 UI 模块、定义 `FORWARD_GROUPS` 配置分组（调度 / 内容 / 过滤 / 转发 / QQ / Telegram / Web / 高级）。
- **样式入口**: `assets/style.css` — 仅由 `@import` 行组成，由 `build_frontend.py` 在编译时内联打包为单文件。

## 对外接口
- 前端通过 `assets/js/api.js` 的 `apiRequest()` 调用后端 Web API（`/api/auth/check`、`/api/config`、`/api/qq/groups`、`/api/tg/channels`、`/api/login/*`、`/api/runtime/*`、`/api/export/*`、`/api/import/*` 等）。
- `isDashboardPage()` 适配器自动识别运行环境（独立 Flask / 插件沙箱），切换 baseURL 与 token 存储位置。

## 关键依赖与配置
- **GSAP**: 本地化打包于 `assets/js/gsap.min.js`（移除 CDN 依赖以解决国内网络卡顿；详见 `docs/superpowers/specs/2026-07-02-frontend-optimization-design.md`）。
- **无外部 npm 依赖**：全部为原生 ES module + 浏览器 API；`store.js` 提供轻量状态管理。
- 设计 token 与配色集中在 `assets/css/variables.css`；组件样式在 `components.css`；频道规则专属样式在 `section-channels.css`。

## 数据模型
- `store.js`: 持有 `state.config` / `state.status` / `state.qqGroups` / `state.tgChannels` 等，并通过订阅模式广播变更。
- `config.js:FORWARD_GROUPS`: 配置表单的结构化描述（key / label / type / defaultValue / suffix / placeholder），驱动 `ui_config.js` 动态渲染表单。
- `MSG_TYPES`: `["文字","图片","视频","音频","文件"]`；`TRI_STATE`: `["继承全局","开启","关闭"]`。

## 测试与质量
- `tests/test_web_frontend_assets.py` 强制校验 `pages/dashboard/` 与 `web/` 同步：改完 `web/` 必须 `python scripts/build_frontend.py`，否则 CI 失败。
- `motionEnabled()` 守卫（`utils.js`）尊重用户偏好与无障碍设置，禁用动效时跳过 GSAP 调用。

## 常见问题 (FAQ)
- **Q**: 登录页点"发送验证码"会覆盖其它页面刚保存的配置吗？
  **A**: 不会。`ui_login.js` 的 `saveChangedLoginConfig()` 先 GET `/api/config` 读服务器最新值，只 patch 用户在当前页面真正改动的 `api_id` / `api_hash` / `proxy_config` 字段，未改动字段不写回。
- **Q**: 为什么 `style.css` 只允许写 `@import`？
  **A**: AstrBot 插件页沙箱无法解析子路径 `@import`，`build_frontend.py` 会把所有 `@import` 内联成单一自包含文件。直接写普通 CSS 会被构建器拒绝。
- **Q**: 改完前端为什么打开还是旧版本？
  **A**: 浏览器缓存。`build_frontend.py` 会自动给 `index.html` 的资源引用加 `?v=<content-hash>` 缓存戳，无需手动 bump。
- **Q: GSAP 动画在 Headless / CI 中卡帧？
  **A**: 参见记忆库 `headless-gsap-testing-gotcha.md` —— `lagSmoothing` 在无渲染环境下会卡出假象，排查时优先禁用。

## 相关文件清单
- `index.html` — 应用外壳与导航（监控：运行总览 / 转发关系；规则：频道规则 / 登录）
- `assets/app.js` — JS 入口与配置分组定义
- `assets/style.css` — `@import` 打包入口
- `assets/css/variables.css` — 设计 token
- `assets/css/base.css` — 基础重置与布局
- `assets/css/components.css` — 通用组件样式
- `assets/css/section-channels.css` — 频道规则表单样式
- `assets/js/api.js` — API 调用与运行环境适配
- `assets/js/config.js` — 前端常量 / 表单字段定义
- `assets/js/store.js` — 状态管理
- `assets/js/context.js` — 全局工具（toast / loader / 物理卡片绑定等）
- `assets/js/ui_login.js` — Telegram 登录流程 UI
- `assets/js/ui_overview.js` — 运行总览 UI
- `assets/js/ui_channels.js` — 频道规则与合并规则 UI
- `assets/js/ui_config.js` — 配置表单渲染 UI
- `assets/js/ui_topology.js` — 转发关系拓扑 UI
- `assets/js/ui_selector.js` — QQ 群 / Telegram 频道选择器
- `assets/js/utils.js` — 通用工具函数（含 `motionEnabled`）
- `assets/js/gsap.min.js` — 本地化 GSAP 动画库

## 变更记录 (Changelog)
- **2026-08-16**: 补充登录保存行为 FAQ（`saveChangedLoginConfig` 只 patch 已改动字段）。
- **2026-08-15**: 补全 `config.js` / `ui_config.js` / `ui_topology.js` 到相关文件清单。
- **2026-07-04**: 初始化模块文档。聚焦 PR33（Web Admin QQ 群选择）与前端性能优化（GSAP 本地化、微动画）。
