from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def _astrbot_version_specifier() -> SpecifierSet:
    for line in (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("astrbot_version:"):
            _, raw_specifier = line.split(":", 1)
            return SpecifierSet(raw_specifier.strip().strip("\"'"))
    raise AssertionError("metadata.yaml must declare astrbot_version")


def test_metadata_requires_astrbot_web_api_host_version() -> None:
    specifier = _astrbot_version_specifier()

    assert Version("4.25.6") not in specifier
    assert Version("4.26.0") in specifier
