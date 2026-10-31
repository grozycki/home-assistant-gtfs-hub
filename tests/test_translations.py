"""Both languages hold the same keys, and code uses keys that exist."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent / "custom_components" / "gtfs_hub"


def keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return set().union(*(keys(item, f"{prefix}/{key}") for key, item in value.items()))


def load(language: str) -> dict[str, dict[str, object]]:
    data: dict[str, dict[str, object]] = json.loads(
        (ROOT / "translations" / f"{language}.json").read_text(encoding="utf-8")
    )
    return data


def test_languages_match() -> None:
    assert keys(load("en")) == keys(load("pl"))


def test_used_keys_exist() -> None:
    english = load("en")
    code = "".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.py"))
    for reason in ("catalogue_not_ready", "entry_not_loaded", "no_lines", "already_configured"):
        assert reason in code
    subentries = json.dumps(english["config_subentries"])
    assert "catalogue_not_ready" in subentries
    assert "no_lines" in subentries
    for key in english["exceptions"]:
        assert key in code
    errors = english["config"]["error"]
    assert isinstance(errors, dict)
    for key in errors:
        assert key in code
