"""Guard the GitHub source zip used by AstrBot plugin install.

AstrBot installs from codeload/git-archive style zips. On Windows the install
prefix is already deep (launcher instance UUID + plugin name + commit hash), so
long non-runtime paths inside the zip blow past MAX_PATH and surface as
FileNotFoundError during unzip (#49).
"""

from __future__ import annotations

import os
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


def _git_env() -> dict[str, str]:
    """Let cwd select the repository, index and object store, even inside hooks."""
    overrides = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
    return {
        key: value for key, value in os.environ.items() if key.upper() not in overrides
    }


def _archive_tree_ish(root: Path = ROOT) -> str:
    """Prefer the index tree so uncommitted packaging fixes are still verifiable."""
    result = subprocess.run(
        ["git", "write-tree"],
        cwd=root,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "HEAD"


def _git_archive_paths(root: Path = ROOT) -> list[str]:
    """List paths that would appear in a GitHub source / git-archive package."""
    tree_ish = _archive_tree_ish(root)
    with tempfile.TemporaryDirectory(prefix="tgfwd-archive-") as tmp:
        archive_path = Path(tmp) / "plugin.tar"
        result = subprocess.run(
            ["git", "archive", "--format=tar", "-o", str(archive_path), tree_ish],
            cwd=root,
            env=_git_env(),
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


@pytest.fixture(
    params=[
        None,
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "combined",
    ],
    ids=lambda value: value or "clean-env",
)
def foreign_git_environment(tmp_path: Path, monkeypatch, request):
    """Point inherited Git overrides at a disposable, unrelated repository."""
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "sentinel.txt").write_text("keep this index intact\n", encoding="utf-8")
    for args in (["init", "--quiet"], ["add", "--", "sentinel.txt"]):
        subprocess.run(
            ["git", *args],
            cwd=foreign,
            env=clean_env,
            capture_output=True,
            text=True,
            check=True,
        )
    git_dir = foreign / ".git"
    preserved = {name: (git_dir / name).read_bytes() for name in ("index", "config")}
    overrides = {
        "GIT_DIR": str(git_dir),
        "GIT_INDEX_FILE": str(git_dir / "index"),
        "GIT_WORK_TREE": str(foreign),
        "GIT_COMMON_DIR": str(git_dir),
        "GIT_OBJECT_DIRECTORY": str(git_dir / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(git_dir / "objects"),
    }
    for name in overrides:
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        if request.param in (name, "combined"):
            monkeypatch.setenv(name, value)
    return foreign, overrides, preserved


def test_tracked_docs_are_excluded_from_install_archive(
    tmp_path: Path, foreign_git_environment, monkeypatch
) -> None:
    """Docs belong in Git, but even deeply nested docs must not ship (#49)."""
    foreign, overrides, preserved = foreign_git_environment
    # An isolated index verifies new tracked docs without changing the real index.
    (tmp_path / ".gitattributes").write_bytes((ROOT / ".gitattributes").read_bytes())
    (tmp_path / "main.py").write_text("# Runtime entry point\n", encoding="utf-8")
    docs = (
        "docs/adr/decision.md",
        "docs/specs/nested/design/research/windows/install/long-path-notes.md",
    )
    for relative_path in docs:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Developer documentation\n", encoding="utf-8")
    try:
        for args in (
            ["init", "--quiet"],
            ["add", "--", ".gitattributes", "main.py", "docs"],
        ):
            subprocess.run(
                ["git", *args],
                cwd=tmp_path,
                env=_git_env(),
                capture_output=True,
                text=True,
                check=True,
            )
        paths = _git_archive_paths(tmp_path)

        # The tree must stay usable after the caller's object database goes away.
        for name in overrides:
            monkeypatch.delenv(name, raising=False)
        assert sorted(_git_archive_paths(tmp_path)) == sorted(paths)
    except pytest.skip.Exception as exc:
        pytest.fail(
            f"Git environment contamination must not silently skip coverage: {exc}"
        )
    finally:
        for name, content in preserved.items():
            assert (foreign / ".git" / name).read_bytes() == content, (
                f"foreign {name} modified"
            )

    assert "main.py" in paths, "install archive must retain runtime files"
    leaked = [path for path in paths if path.startswith("docs/")]
    assert leaked == [], f"tracked docs leaked into install archive: {leaked}"
