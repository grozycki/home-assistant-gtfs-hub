"""The manifest agrees with the package and pins the libraries it is tested with."""

import json
import tomllib
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).parent.parent
MANIFEST = ROOT / "custom_components" / "gtfs_hub" / "manifest.json"


def _manifest() -> dict[str, object]:
    data: dict[str, object] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data


def test_manifest_version_matches_package() -> None:
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert _manifest()["version"] == package["project"]["version"]


def test_requirements_pin_tested_libraries() -> None:
    requirements = _manifest()["requirements"]
    assert isinstance(requirements, list)
    assert set(requirements) == {
        f"gtfs-hub=={version('gtfs-hub')}",
        f"gtfs-hub-gzm=={version('gtfs-hub-gzm')}",
    }
