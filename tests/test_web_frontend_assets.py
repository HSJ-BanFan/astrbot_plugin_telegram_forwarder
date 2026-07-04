from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_ASSETS = ROOT / "web" / "assets"
PAGE_ROOT = ROOT / "pages" / "dashboard"
PAGE_ASSETS = PAGE_ROOT / "assets"
IMPORT_RE = re.compile(r"""^\s*import\s+(?:[^'"]+\s+from\s+)?['"](?P<path>\.{1,2}/[^'"]+)['"]""")


def test_dashboard_plugin_page_entry_exists() -> None:
    assert (PAGE_ROOT / "index.html").is_file()


def test_dashboard_plugin_page_skips_legacy_token_auth() -> None:
    text = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="authScreen"' not in text
    assert 'id="authForm"' not in text
    assert 'id="tokenInput"' not in text
    assert "访问 Token" not in text.split('id="appShell"', 1)[0]
    assert 'href="./assets/style.css"' in text
    assert 'src="./assets/app.js"' in text
    assert 'href="/assets/style.css"' not in text
    assert 'src="/assets/app.js"' not in text


def test_dashboard_plugin_page_title_i18n_exists() -> None:
    i18n_root = ROOT / ".astrbot-plugin" / "i18n"
    for locale in ("zh-CN", "en-US"):
        payload = json.loads((i18n_root / f"{locale}.json").read_text(encoding="utf-8"))
        assert payload["pages"]["dashboard"]["title"]


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


def test_dashboard_page_uses_bridge_compatible_request_layer() -> None:
    text = (PAGE_ASSETS / "js" / "api.js").read_text(encoding="utf-8")

    assert "window.AstrBotPluginPage" in text
    assert "bridge.apiGet(endpoint" in text
    assert "bridge.apiPost(endpoint" in text


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
    text = (WEB_ASSETS / "app.js").read_text(encoding="utf-8")

    assert 'payload.type === "tg"' in text
    assert 'payload.type === "qq"' in text
    assert "addTopologyDefaultTarget" not in text
    assert "请将 QQ 群拖到左侧 Telegram 频道节点上建立专属关系。" in text


def test_topology_nodes_expose_context_menu_removal_actions() -> None:
    app_text = (WEB_ASSETS / "app.js").read_text(encoding="utf-8")
    css_text = (WEB_ASSETS / "css" / "components.css").read_text(encoding="utf-8")

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
