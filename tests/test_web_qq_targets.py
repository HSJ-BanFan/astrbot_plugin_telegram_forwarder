from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATHS = (
    ROOT / "web" / "assets" / "js" / "qq_targets.js",
    ROOT / "pages" / "dashboard" / "assets" / "js" / "qq_targets.js",
)

HARNESS = textwrap.dedent(
    """
    const { pathToFileURL } = require("node:url");

    (async () => {
      const module = await import(pathToFileURL(process.env.QQ_TARGETS_MODULE).href);
      const official = module.parseManualQQTarget(
        " default_1906433733 : GroupMessage : 35D268C5BC2B9A04321625F380F13643 ",
      );
      const numeric = module.parseManualQQTarget("12345");
      const invalid = module.parseManualQQTarget("p:FriendMessage:12345");
      process.stdout.write(JSON.stringify({
        official,
        numeric,
        invalid,
        numericAfterSession: module.mergeQQTargetValue(
          ["p:GroupMessage:12345"],
          "12345",
        ),
        sessionAfterNumeric: module.mergeQQTargetValue(
          ["12345"],
          "p:GroupMessage:12345",
        ),
        distinctPlatforms: module.mergeQQTargetValue(
          ["p:GroupMessage:12345"],
          "q:GroupMessage:12345",
        ),
      }));
    })();
    """
).strip()


def run_qq_targets_module(module_path: Path) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on the development machine
        pytest.skip("node is not available")
    completed = subprocess.run(
        [node, "-e", HARNESS],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env={**os.environ, "QQ_TARGETS_MODULE": str(module_path)},
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    "module_path",
    MODULE_PATHS,
    ids=["web-source", "dashboard-build"],
)
def test_qq_target_parser_normalizes_official_sessions(module_path: Path) -> None:
    result = run_qq_targets_module(module_path)

    assert result["official"]["target"] == (
        "default_1906433733:"
        "GroupMessage:35D268C5BC2B9A04321625F380F13643"
    )
    assert result["official"]["group_id"] == "35D268C5BC2B9A04321625F380F13643"
    assert result["official"]["group"]["avatar"] == ""
    assert result["numeric"]["target"] == "12345"
    assert result["numeric"]["group"]["avatar"].endswith(
        "/gh/12345/12345/640"
    )
    assert result["invalid"] is None


@pytest.mark.parametrize(
    "module_path",
    MODULE_PATHS,
    ids=["web-source", "dashboard-build"],
)
def test_qq_target_merge_prefers_specific_session_without_cross_platform_dedupe(
    module_path: Path,
) -> None:
    result = run_qq_targets_module(module_path)

    assert result["numericAfterSession"] == ["p:GroupMessage:12345"]
    assert result["sessionAfterNumeric"] == ["p:GroupMessage:12345"]
    assert result["distinctPlatforms"] == [
        "p:GroupMessage:12345",
        "q:GroupMessage:12345",
    ]
