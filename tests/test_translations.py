"""Both languages hold the same keys, and code uses keys that exist."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "gtfs_hub"


def keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return set().union(*(keys(item, f"{prefix}/{key}") for key, item in value.items()))


def load(language: str) -> dict:
    return json.loads((ROOT / "translations" / f"{language}.json").read_text(encoding="utf-8"))


def test_languages_match() -> None:
    assert keys(load("en")) == keys(load("pl"))


def test_used_keys_exist() -> None:
    english = load("en")
    code = "".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.py"))
    for reason in ("catalogue_not_ready", "entry_not_loaded", "no_lines", "already_configured"):
        assert reason in code
    assert "catalogue_not_ready" in english["config_subentries"]["line"]["abort"]
    assert "no_lines" in english["config_subentries"]["stop"]["abort"]
    for key in english["exceptions"]:
        assert key in code
    for key in english["config"]["error"]:
        assert key in code
