"""Prefixed tracker IDs, their one-time migration, and the recorder issue."""

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DEMO_VEHICLES_VARIABLE, DOMAIN
from custom_components.gtfs_hub.recorder_load import ISSUE_THRESHOLD
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from conftest import FakeHub, line_subentry, make_entry, stop_subentry

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("demo")
RECORDED = "homeassistant.components.recorder.entity_options.is_entity_recorded"


@pytest.fixture
def demo() -> Generator[None]:
    with patch.dict("os.environ", {DEMO_VEHICLES_VARIABLE: "1"}):
        yield


def line_id(entry: MockConfigEntry) -> str:
    return str(
        next(item.subentry_id for item in entry.subentries.values() if item.title == "Bus 11")
    )


def add_tracker(
    hass: HomeAssistant, entry: MockConfigEntry, unique: str, object_id: str, name: str
) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create(
            "device_tracker",
            DOMAIN,
            f"{line_id(entry)}_{unique}",
            config_entry=entry,
            config_subentry_id=line_id(entry),
            suggested_object_id=object_id,
            original_name=name,
        )
        .entity_id
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def old_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = make_entry(line_subentry(), stop_subentry())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, minor_version=1)
    return entry


async def test_old_trackers_are_renamed_once(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = old_entry(hass)
    numbered = add_tracker(hass, entry, "108_108001347", "11_1347", "11 #1347")
    keyed = add_tracker(hass, entry, "v1", "11_v1", "11 v1")
    await setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(numbered) is None
    assert registry.async_get("device_tracker.gtfs_hub_fake_11_1347") is not None
    assert registry.async_get(keyed) is None
    assert registry.async_get("device_tracker.gtfs_hub_fake_11_v1") is not None
    assert entry.minor_version == 2
    # A rename by the user afterwards stays.
    registry.async_update_entity(
        "device_tracker.gtfs_hub_fake_11_1347", new_entity_id="device_tracker.my_tram"
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("device_tracker.my_tram") is not None


async def test_taken_id_keeps_the_old_one(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = old_entry(hass)
    er.async_get(hass).async_get_or_create(
        "device_tracker", "other", "x", suggested_object_id="gtfs_hub_fake_11_1347"
    )
    old = add_tracker(hass, entry, "108_108001347", "11_1347", "11 #1347")
    await setup(hass, entry)
    assert er.async_get(hass).async_get(old) is not None


def many_trackers(hass: HomeAssistant, count: int) -> MockConfigEntry:
    entry = make_entry(line_subentry(), stop_subentry())
    entry.add_to_hass(hass)
    for number in range(count):
        add_tracker(hass, entry, f"v{number}", f"gtfs_hub_fake_11_v{number}", f"11 v{number}")
    return entry


def issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"trackers_recorded_{entry.entry_id}")


async def test_many_recorded_trackers_raise_an_issue(hass: HomeAssistant, hub: FakeHub) -> None:
    hass.config.components.add("recorder")
    entry = many_trackers(hass, ISSUE_THRESHOLD)
    with patch(RECORDED, return_value=True):
        await setup(hass, entry)
    found = issue(hass, entry)
    assert found is not None
    assert found.translation_placeholders is not None
    assert found.translation_placeholders["pattern"] == "device_tracker.gtfs_hub_*"
    assert found.severity == ir.IssueSeverity.WARNING
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, entry) is None


async def test_excluded_trackers_raise_nothing(hass: HomeAssistant, hub: FakeHub) -> None:
    hass.config.components.add("recorder")
    entry = many_trackers(hass, ISSUE_THRESHOLD)
    with patch(RECORDED, return_value=False):
        await setup(hass, entry)
    assert issue(hass, entry) is None


async def test_few_trackers_raise_nothing(hass: HomeAssistant, hub: FakeHub) -> None:
    hass.config.components.add("recorder")
    entry = many_trackers(hass, ISSUE_THRESHOLD - 1)
    with patch(RECORDED, return_value=True):
        await setup(hass, entry)
    assert issue(hass, entry) is None


async def test_no_recorder_raises_nothing(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = many_trackers(hass, ISSUE_THRESHOLD)
    await setup(hass, entry)
    assert issue(hass, entry) is None


async def test_prefixed_and_nameless_trackers_stay(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = old_entry(hass)
    prefixed = add_tracker(hass, entry, "v1", "gtfs_hub_fake_11_v1", "11 v1")
    nameless = add_tracker(hass, entry, "v2", "odd", "")
    await setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(prefixed) is not None
    assert registry.async_get(nameless) is not None


async def test_newer_entry_version_is_refused(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make_entry(line_subentry())
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=2)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
