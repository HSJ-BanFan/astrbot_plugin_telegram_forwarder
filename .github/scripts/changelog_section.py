#!/usr/bin/env python3
"""从 CHANGELOG.md 中抽取与发布 tag 对应的版本段落，写入 notes.md。

用法::

    python changelog_section.py <tag>   # 例如 v0.8.0

行为：
- 以 UTF-8 读取 CHANGELOG.md，定位首个匹配该 tag 的 ``## `` 版本标题，
  抽取其后正文（到下一个 ``## `` 标题为止）写入 notes.md（UTF-8）。
- 容忍标题版本号段数少于 tag 的情况：标题 ``## v0.8`` 可匹配 tag ``v0.8.0``。
- 在 GitHub Actions 中运行时，向 ``$GITHUB_OUTPUT`` 写入 ``found=true|false``，
  供后续步骤决定使用 notes.md 还是回退到自动生成的发行说明。
"""

import os
import re
import sys
from pathlib import Path


def _components(version: str) -> list[str]:
    """把版本号拆成非空段列表，例如 ``0.8.0`` -> ``["0", "8", "0"]``。"""
    return [part for part in version.split(".") if part]


def _is_prefix(short: str, long: str) -> bool:
    """判断 ``short`` 的版本段是否为 ``long`` 的前缀（按 ``.`` 分段比较）。

    Args:
        short: 较短的版本号字符串（如标题中的 ``0.8``）。
        long: 较长的版本号字符串（如 tag 的 ``0.8.0``）。

    Returns:
        当 ``short`` 的每一段都与 ``long`` 对应位置相等时返回 True。
    """
    short_parts, long_parts = _components(short), _components(long)
    return bool(short_parts) and long_parts[: len(short_parts)] == short_parts


def extract(tag: str, changelog: Path) -> str:
    """抽取与 ``tag`` 对应的 CHANGELOG 段落正文。

    Args:
        tag: 发布 tag，例如 ``v0.8.0``（前导 ``v`` 可有可无）。
        changelog: CHANGELOG.md 文件路径。

    Returns:
        匹配段落的正文（已去除首尾空白）；未找到时返回空字符串。
    """
    version = tag.lstrip("v").strip()
    if not changelog.exists():
        return ""

    lines = changelog.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        match = re.search(r"v?(\d+(?:\.\d+)*)", line)
        if not match:
            continue
        heading = match.group(1)
        if (
            heading == version
            or _is_prefix(heading, version)
            or _is_prefix(version, heading)
        ):
            start = index
            break

    if start is None:
        return ""

    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def main() -> int:
    """命令行入口：写出 notes.md 并向 GITHUB_OUTPUT 报告是否命中。"""
    if len(sys.argv) < 2:
        print("usage: changelog_section.py <tag>", file=sys.stderr)
        return 2

    tag = sys.argv[1]
    notes = extract(tag, Path("CHANGELOG.md"))
    Path("notes.md").write_text(notes + "\n", encoding="utf-8")

    found = "true" if notes else "false"
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"found={found}\n")
    print(f"tag={tag} found={found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
