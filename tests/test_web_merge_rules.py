import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("frontend", ["web", "pages/dashboard"])
def test_merge_editor_preserves_config_and_validates_drafts(frontend, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    assets = tmp_path / "assets"
    shutil.copytree(ROOT / frontend / "assets", assets)
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "frontend" / "merge_rules.mjs")],
        env={**os.environ, "MERGE_ASSETS": assets.as_uri()},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
