"""Guard the GitHub source zip used by AstrBot plugin install.

AstrBot installs from codeload/git-archive style zips. On Windows the install
prefix is already deep (launcher instance UUID + plugin name + commit hash), so
long non-runtime paths inside the zip blow past MAX_PATH and surface as
FileNotFoundError during unzip (#49).
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Paths that must never ship in the install archive (developer / CI only).
EXPORT_IGNORED_PREFIXES = (
    "docs/",
    "tests/",
    "scripts/",
    ".github/",
    "resources/",
    ".context/",
    ".claude/",
    ".superpowers/",
)
EXPORT_IGNORED_FILES = {
    "CLAUDE.md",
    "pyrightconfig.example.json",
}

# Runtime surfaces required after install (Web admin + AstrBot dashboard page).
REQUIRED_ARCHIVE_PATHS = (
    "main.py",
    "metadata.yaml",
    "requirements.txt",
    "_conf_schema.json",
    "web/index.html",
    "pages/dashboard/index.html",
    ".astrbot-plugin/i18n/zh-CN.json",
)

# Issue #49 used a ~207-char Windows install prefix. Keep archive members short
# enough that prefix + relative path stays under classic MAX_PATH (260).
ISSUE49_INSTALL_PREFIX_LEN = 207
WINDOWS_MAX_PATH = 260


def _archive_tree_ish() -> str:
    """Prefer the index tree so uncommitted packaging fixes are still verifiable."""
    result = subprocess.run(
        ["git", "write-tree"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "HEAD"


def _git_archive_paths() -> list[str]:
    """List paths that would appear in a GitHub source / git-archive package."""
    tree_ish = _archive_tree_ish()
    with tempfile.TemporaryDirectory(prefix="tgfwd-archive-") as tmp:
        archive_path = Path(tmp) / "plugin.tar"
        result = subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(archive_path), tree_ish],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"git archive unavailable: {result.stderr.strip()}")

        with tarfile.open(archive_path, "r:") as tar:
            return [member.name for member in tar.getmembers() if member.isfile()]


def _is_export_ignored(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in EXPORT_IGNORED_FILES:
        return True
    if normalized.endswith("/CLAUDE.md") or normalized == "CLAUDE.md":
        return True
    return any(normalized.startswith(prefix) for prefix in EXPORT_IGNORED_PREFIXES)


def test_gitattributes_declares_install_export_ignores() -> None:
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for prefix in EXPORT_IGNORED_PREFIXES:
        assert f"{prefix} export-ignore" in text, f"missing export-ignore for {prefix}"
    for name in EXPORT_IGNORED_FILES:
        assert f"{name} export-ignore" in text, f"missing export-ignore for {name}"
    assert "**/CLAUDE.md export-ignore" in text


def test_git_archive_excludes_developer_assets() -> None:
    paths = _git_archive_paths()
    assert paths, "git archive produced an empty file list"

    leaked = sorted(path for path in paths if _is_export_ignored(path))
    assert leaked == [], f"install archive still contains developer assets: {leaked}"

    missing = [path for path in REQUIRED_ARCHIVE_PATHS if path not in paths]
    assert missing == [], f"install archive missing runtime files: {missing}"


def test_git_archive_paths_fit_windows_max_path_under_issue49_prefix() -> None:
    paths = _git_archive_paths()
    budget = WINDOWS_MAX_PATH - ISSUE49_INSTALL_PREFIX_LEN
    offenders = sorted(
        (len(path), path) for path in paths if len(path.replace("\\", "/")) > budget
    )
    assert offenders == [], (
        "archive relative paths too long for Windows install prefix "
        f"(budget {budget} chars): {offenders[:10]}"
    )


def test_docs_are_not_tracked_in_git() -> None:
    """docs/ is gitignored developer material; force-adds reintroduce #49."""
    result = subprocess.run(
        ["git", "ls-files", "--", "docs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    assert tracked == [], f"docs/ should not be tracked, found: {tracked}"
