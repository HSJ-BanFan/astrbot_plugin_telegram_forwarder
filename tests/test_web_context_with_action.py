"""`withAction()` 的行为测试：action 成功后刷新失败不得被报成 action 失败。

前端没有 JS 测试框架，这里用 Node 的 vm 把 `withAction` 单独抽出来跑；
Node 不可用时跳过（`build_frontend` 产物同步仍由 test_web_frontend_assets.py 保证）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SOURCES = (
    ROOT / "web" / "assets" / "js" / "context.js",
    ROOT / "pages" / "dashboard" / "assets" / "js" / "context.js",
)
WITH_ACTION_RE = re.compile(
    r"^export async function withAction\(.*?^\}", re.MULTILINE | re.DOTALL
)

HARNESS = textwrap.dedent(
    """
    const vm = require("node:vm");
    const source = JSON.parse(process.env.WITH_ACTION_SOURCE);
    const scenario = process.env.WITH_ACTION_SCENARIO;

    const toasts = [];
    const refreshed = [];
    const sandbox = {
      console: { warn() {} },
      showToast: (message) => toasts.push(message),
      loadStatusOnly: async () => {
        refreshed.push("status");
        if (scenario === "refresh-fails") throw new Error("登录已过期，请重新输入 Token");
      },
      loadAll: async (opts) => {
        refreshed.push(opts && opts.force ? "all:force" : "all");
        if (scenario === "refresh-fails") throw new Error("登录已过期，请重新输入 Token");
      },
    };
    vm.createContext(sandbox);
    vm.runInContext(source + "\\nglobalThis.__withAction = withAction;", sandbox);

    const actions = {
      "ok": async () => ({ ok: true }),
      "refresh-fails": async () => ({ ok: true }),
      "action-fails": async () => { throw new Error("清空队列失败"); },
      "result-message": async () => ({ message: "已清空 3 条" }),
    };

    (async () => {
      const returned = await sandbox.__withAction(actions[scenario], "操作完成。", {
        refresh: scenario === "result-message" ? "status" : "all",
      });
      process.stdout.write(JSON.stringify({ toasts, refreshed, returned }));
    })();
    """
).strip()


def run_scenario(source_path: Path, scenario: str) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - 取决于开发机是否装了 Node
        pytest.skip("node is not available")
    match = WITH_ACTION_RE.search(source_path.read_text(encoding="utf-8"))
    assert match, f"未能从 {source_path} 中提取 withAction"
    body = match.group(0).replace("export async function", "async function", 1)
    completed = subprocess.run(
        [node, "-e", HARNESS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={
            **os.environ,
            "WITH_ACTION_SOURCE": json.dumps(body),
            "WITH_ACTION_SCENARIO": scenario,
        },
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    "source_path", CONTEXT_SOURCES, ids=["web-source", "dashboard-build"]
)
class TestWithAction:
    def test_success_reports_done_message(self, source_path: Path) -> None:
        out = run_scenario(source_path, "ok")

        assert out["toasts"] == ["操作完成。"]
        assert out["refreshed"] == ["all"]
        assert out["returned"] == {"ok": True}

    def test_refresh_failure_does_not_mask_successful_action(
        self, source_path: Path
    ) -> None:
        out = run_scenario(source_path, "refresh-fails")

        # action 成功了：返回值必须保留，提示必须仍是成功文案。
        assert out["returned"] == {"ok": True}
        assert len(out["toasts"]) == 1
        assert out["toasts"][0].startswith("操作完成。")
        assert "页面刷新失败" in out["toasts"][0]

    def test_action_failure_still_reports_failure(self, source_path: Path) -> None:
        out = run_scenario(source_path, "action-fails")

        assert out["toasts"] == ["清空队列失败"]
        assert out["returned"] is None
        # action 失败时不该再去刷新。
        assert out["refreshed"] == []

    def test_result_message_overrides_done_message(self, source_path: Path) -> None:
        out = run_scenario(source_path, "result-message")

        assert out["toasts"] == ["已清空 3 条"]
        assert out["refreshed"] == ["status"]
