from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_ASSETS = ROOT / "web" / "assets"
PAGE_ROOT = ROOT / "pages" / "dashboard"
PAGE_ASSETS = PAGE_ROOT / "assets"
IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:[^'"]+\s+from\s+)?['"](?P<path>\.{1,2}/[^'"]+)['"]"""
)


def _load_build_frontend():
    spec = importlib.util.spec_from_file_location(
        "build_frontend", ROOT / "scripts" / "build_frontend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_frontend = _load_build_frontend()
ASSET_VERSION = build_frontend.compute_version(build_frontend.generate_assets())


def test_dashboard_plugin_page_entry_exists() -> None:
    assert (PAGE_ROOT / "index.html").is_file()
    assert (PAGE_ROOT / "_page.json").is_file()


def test_dashboard_plugin_page_skips_legacy_token_auth() -> None:
    text = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="authScreen"' not in text
    assert 'id="authForm"' not in text
    assert 'id="tokenInput"' not in text
    assert "访问 Token" not in text.split('id="appShell"', 1)[0]
    assert f'href="./assets/style.css?v={ASSET_VERSION}"' in text
    assert f'src="./assets/app.js?v={ASSET_VERSION}"' in text
    assert 'href="/assets/style.css"' not in text
    assert 'src="/assets/app.js"' not in text


def test_dashboard_plugin_page_loads_bridge_before_app() -> None:
    text = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")

    bridge_pos = text.index('src="/api/plugin/page/bridge-sdk.js"')
    app_pos = text.index(f'src="./assets/app.js?v={ASSET_VERSION}"')
    assert bridge_pos < app_pos


def test_dashboard_plugin_page_title_i18n_exists() -> None:
    i18n_root = ROOT / ".astrbot-plugin" / "i18n"
    for locale in ("zh-CN", "en-US"):
        payload = json.loads((i18n_root / f"{locale}.json").read_text(encoding="utf-8"))
        assert payload["pages"]["dashboard"]["title"]
        assert payload["pages"]["dashboard"]["description"]


def test_frontend_relative_module_imports_resolve_for_all_entrypoints() -> None:
    asset_roots = [WEB_ASSETS, PAGE_ASSETS]
    for asset_root in asset_roots:
        assert (asset_root / "app.js").is_file()
    for asset_root in asset_roots:
        for source in [asset_root / "app.js", *(asset_root / "js").glob("*.js")]:
            text = source.read_text(encoding="utf-8")
            for match in IMPORT_RE.finditer(text):
                target = (source.parent / match.group("path")).resolve()
                assert target.is_file(), (
                    f"{source.relative_to(ROOT)} imports missing module "
                    f"{match.group('path')}"
                )


def test_entrypoints_boot_after_domcontentloaded_if_module_loads_late() -> None:
    for asset_root in (WEB_ASSETS, PAGE_ASSETS):
        text = (asset_root / "app.js").read_text(encoding="utf-8")

        assert 'document.readyState === "loading"' in text
        assert (
            'document.addEventListener("DOMContentLoaded", boot, { once: true })'
            in text
        )
        assert "boot();" in text


def test_dashboard_page_uses_bridge_compatible_request_layer() -> None:
    text = (PAGE_ASSETS / "js" / "api.js").read_text(encoding="utf-8")

    assert "window.AstrBotPluginPage" in text
    assert 'window.location.pathname.startsWith("/api/plugin/page/content/")' in text
    assert "async function waitForDashboardBridge" in text
    assert "return `page/${legacyEndpoint}`" in text
    assert "bridge.apiGet(endpoint" in text
    assert "bridge.apiPost(endpoint" in text


def test_dashboard_page_has_visible_bridge_failure_state() -> None:
    context_text = (PAGE_ASSETS / "js" / "context.js").read_text(encoding="utf-8")
    app_text = (PAGE_ASSETS / "app.js").read_text(encoding="utf-8")

    assert "renderDashboardLoadError" in context_text
    assert "Dashboard 插件页加载失败" in context_text
    assert "data-dashboard-load-error" in context_text
    assert "catch (error)" in app_text
    assert "renderDashboardLoadError(error)" in app_text


def test_dashboard_page_boot_uses_aggregate_page_api() -> None:
    context_text = (PAGE_ASSETS / "js" / "context.js").read_text(encoding="utf-8")

    assert 'apiRequest("/api/dashboard")' in context_text
    assert "dashboardPayload.status" in context_text
    assert "dashboardPayload.errors" in context_text


def test_dashboard_page_uses_sandbox_safe_storage_helpers() -> None:
    utils_text = (PAGE_ASSETS / "js" / "utils.js").read_text(encoding="utf-8")
    assert "safeStorageGet" in utils_text
    assert "safeStorageSet" in utils_text
    assert "safeStorageRemove" in utils_text

    for source in [PAGE_ASSETS / "app.js", *(PAGE_ASSETS / "js").glob("*.js")]:
        if source.name == "utils.js":
            continue
        text = source.read_text(encoding="utf-8")
        assert "localStorage" not in text, (
            f"{source.relative_to(ROOT)} directly accesses localStorage"
        )


def test_dashboard_backend_registers_page_api_namespace() -> None:
    text = (ROOT / "main.py").read_text(encoding="utf-8")

    assert '("dashboard", self.dashboard_page_dashboard' in text
    assert 'f"/{PLUGIN_NAME}/page/{route}"' in text
    assert "async def dashboard_page_dashboard" in text
    assert '"qqGroups": qq_groups' in text
    assert '"tgChannels": tg_channels' in text


def test_dashboard_page_stylesheet_is_self_contained() -> None:
    text = (PAGE_ASSETS / "style.css").read_text(encoding="utf-8")

    assert "@import" not in text
    assert ".app-shell" in text
    assert ".nav-item" in text


def test_topology_stage_adapts_to_dashboard_iframe_width() -> None:
    css_files = [
        WEB_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "style.css",
    ]

    for css_file in css_files:
        text = css_file.read_text(encoding="utf-8")
        match = re.search(r"\.topology-stage\s*\{(?P<body>[^}]+)\}", text)
        assert match, f"{css_file.relative_to(ROOT)} missing .topology-stage rule"
        body = match.group("body")
        assert "width: 100%;" in body
        assert "min-width: 0;" in body
        assert "min-width: 920px" not in body


def test_editable_topology_canvas_expands_to_content_height() -> None:
    css_files = [
        WEB_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "style.css",
    ]

    for css_file in css_files:
        text = css_file.read_text(encoding="utf-8")
        match = re.search(
            r"\.target-topology:not\(\.topology-readonly\)\s+\.topology-canvas\s*\{(?P<body>[^}]+)\}",
            text,
        )
        assert match, (
            f"{css_file.relative_to(ROOT)} missing editable topology canvas rule"
        )
        body = match.group("body")
        assert "max-height: none;" in body
        assert "overflow: hidden;" in body


def test_topology_edges_align_with_node_cards_at_any_width() -> None:
    css_files = [
        WEB_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "css" / "components.css",
        PAGE_ASSETS / "style.css",
    ]

    for css_file in css_files:
        text = css_file.read_text(encoding="utf-8")
        stage = re.search(r"\.topology-stage\s*\{(?P<body>[^}]+)\}", text)
        assert stage, f"{css_file.relative_to(ROOT)} missing .topology-stage rule"
        assert "--topology-node-width:" in stage.group("body")
        assert "--topology-node-inset:" in stage.group("body")

        lines = re.search(r"\.topology-lines\s*\{(?P<body>[^}]+)\}", text)
        assert lines, f"{css_file.relative_to(ROOT)} missing .topology-lines rule"
        body = lines.group("body")
        assert (
            "left: calc(var(--topology-node-inset) + var(--topology-node-width));"
            in body
        )
        assert (
            "width: calc(100% - 2 * (var(--topology-node-inset) + var(--topology-node-width)));"
            in body
        )
        assert "overflow: visible;" in body

    for asset_root in (WEB_ASSETS, PAGE_ASSETS):
        text = (asset_root / "app.js").read_text(encoding="utf-8")
        # SVG 画布已被 CSS 夹在两列卡片之间，x 轴 0→100 必须画满全幅才能贴合卡片边缘
        assert 'd="M 0 ${y1} C 35 ${y1}, 65 ${y2}, 100 ${y2}"' in text
        assert 'd="M 28' not in text


def test_generated_dashboard_artifacts_in_sync_with_web_source() -> None:
    """pages/dashboard/ 是生成产物：改了 web/ 或 overrides 后必须重跑构建脚本。"""
    mismatches = build_frontend.build(check=True)
    assert not mismatches, (
        "Generated frontend artifacts drifted from web/ source. "
        "Run `python scripts/build_frontend.py`. Out of sync: " + ", ".join(mismatches)
    )


def test_both_index_html_reference_current_asset_version() -> None:
    web_text = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    page_text = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")

    for text in (web_text, page_text):
        stamps = set(re.findall(r"\?v=([0-9A-Za-z._-]+)", text))
        assert stamps == {ASSET_VERSION}


def test_legacy_web_relative_module_imports_resolve() -> None:
    for source in [WEB_ASSETS / "app.js", *(WEB_ASSETS / "js").glob("*.js")]:
        text = source.read_text(encoding="utf-8")
        for match in IMPORT_RE.finditer(text):
            target = (source.parent / match.group("path")).resolve()
            assert target.is_file(), (
                f"{source.relative_to(ROOT)} imports missing module "
                f"{match.group('path')}"
            )


def test_topology_stage_drop_does_not_add_qq_payload_as_default_target() -> None:
    for asset_root in (WEB_ASSETS, PAGE_ASSETS):
        text = (asset_root / "app.js").read_text(encoding="utf-8")

        assert 'payload.type === "tg"' in text
        assert 'payload.type === "qq"' in text
        assert "addTopologyDefaultTarget" not in text
        assert "请将 QQ 群拖到左侧 Telegram 频道节点上建立专属关系。" in text


def test_topology_nodes_expose_context_menu_removal_actions() -> None:
    targets = [
        (WEB_ASSETS / "app.js", WEB_ASSETS / "css" / "components.css"),
        (PAGE_ASSETS / "app.js", PAGE_ASSETS / "style.css"),
    ]

    for app_path, css_path in targets:
        app_text = app_path.read_text(encoding="utf-8")
        css_text = css_path.read_text(encoding="utf-8")

        assert 'addEventListener("contextmenu"' in app_text
        assert "removeTopologyChannel(index)" in app_text
        assert "removeTopologyTarget(node.dataset.topologyTargetKey)" in app_text
        assert 'data-topology-target-key="${escapeHtml(target.key)}"' in app_text
        assert 'document.addEventListener("pointermove", onPointerMove)' in app_text
        assert "anchorNode.contains(target) || menu.contains(target)" in app_text
        assert 'menu.classList.add("danger-only")' in app_text
        assert ".topology-context-menu" in css_text
        assert ".topology-context-menu.danger-only" in css_text
        assert "@keyframes topology-context-in" in css_text
        assert ".topology-context-menu button.danger::before" in css_text
