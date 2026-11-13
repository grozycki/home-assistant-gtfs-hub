"""Both languages hold the same keys, and code uses keys that exist."""

import json
from pathlib import Path

import pytest
from homeassistant.util.yaml import load_yaml_dict

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
    for reason in ("download_failed", "no_lines"):
        assert reason in code
    assert "download_failed" in json.dumps(english["config"]["abort"])
    assert "no_lines" in json.dumps(english["options"]["error"])
    for key in english["exceptions"]:
        assert key in code
    errors = english["config"]["error"]
    assert isinstance(errors, dict)
    for key in errors:
        assert key in code


def described(kind: str) -> dict[str, set[str]]:
    """Return each trigger or condition of a description file with its fields."""
    found: dict[str, set[str]] = {}
    for key, value in load_yaml_dict(str(ROOT / f"{kind}.yaml")).items():
        if key.startswith(".") or not isinstance(value, dict):
            continue
        fields = value.get("fields", {})
        assert isinstance(fields, dict)
        found[key] = set(fields)
    return found


@pytest.mark.parametrize("kind", ["triggers", "conditions"])
@pytest.mark.parametrize("language", ["en", "pl"])
def test_descriptions_have_strings(kind: str, language: str) -> None:
    strings = load(language)[kind]
    for key, fields in described(kind).items():
        item = strings[key]
        assert isinstance(item, dict)
        assert item["name"]
        assert item["description"]
        assert set(item["fields"]) == fields
