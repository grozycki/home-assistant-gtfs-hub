"""Names, line devices and removal of trackers whose vehicles are gone."""

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DEMO_VEHICLES_VARIABLE, DEPARTURES_INTERVAL, DOMAIN
from custom_components.gtfs_hub.sightings import RETENTION
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import (
    LINE_11_URL,
    TRAM_11,
    FakeHub,
    get_state,
    line_subentry,
    make_entry,
    stop_subentry,
    vehicle,
)
from gtfs_hub import Vehicle, VehicleKey

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

TRACKER = "device_tracker.gtfs_hub_fake_11_v1"

pytestmark = pytest.mark.usefixtures("demo")


@pytest.fixture
def demo() -> Generator[None]:
    with patch.dict("os.environ", {DEMO_VEHICLES_VARIABLE: "1"}):
        yield


def make() -> MockConfigEntry:
    return make_entry(line_subentry(), stop_subentry())


def line_id(entry: MockConfigEntry) -> str:
    return str(
        next(item.subentry_id for item in entry.subentries.values() if item.title == "Bus 11")
    )


def sightings_key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}.sightings"


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, by: timedelta) -> None:
    freezer.tick(by)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def tracked(hass: HomeAssistant) -> list[str]:
    return [item for item in er.async_get(hass).entities if item.startswith("device_tracker.")]


async def test_tracker_named_by_fleet_number(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle(label="1347")]
    await start(hass, make())
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    state = get_state(hass, "device_tracker.gtfs_hub_fake_11_1347")
    assert state.attributes["friendly_name"] == "11 #1347"
    assert state.attributes["fleet_number"] == "1347"


async def test_tracker_without_fleet_number_uses_the_key(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await start(hass, make())
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    state = get_state(hass, TRACKER)
    assert state.attributes["friendly_name"] == "11 v1"
    assert state.attributes["fleet_number"] is None


async def test_tracker_belongs_to_the_line(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    entry = make()
    await start(hass, entry)
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    item = er.async_get(hass).async_get(TRACKER)
    assert item is not None
    assert item.config_subentry_id == line_id(entry)


async def test_vehicle_of_an_untracked_line_gets_no_tracker(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [replace(vehicle(), line=TRAM_11)]
    await start(hass, make())
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    assert tracked(hass) == []


async def test_line_device_exists_without_vehicles(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make()
    await start(hass, entry)
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    [device] = [item for item in devices if (DOMAIN, line_id(entry)) in item.identifiers]
    assert device.name == "Bus 11"
    assert device.configuration_url == LINE_11_URL


def other() -> Vehicle:
    """Keeps the feed alive while the tracked vehicle is away."""
    return replace(vehicle(), key=VehicleKey("v2"))


def stored_tracker(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    unique_id = f"{line_id(entry)}_v1"
    er.async_get(hass).async_get_or_create(
        "device_tracker",
        DOMAIN,
        unique_id,
        config_entry=entry,
        config_subentry_id=line_id(entry),
        suggested_object_id="gtfs_hub_fake_11_v1",
    )
    return unique_id


async def test_vanished_vehicle_is_removed_and_may_return(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await start(hass, make())
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    hub.vehicles = [other()]
    await advance(hass, freezer, RETENTION - timedelta(hours=1))
    assert TRACKER in tracked(hass)
    await advance(hass, freezer, timedelta(hours=2))
    assert TRACKER not in tracked(hass)
    hub.vehicles = [vehicle(), other()]
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    assert TRACKER in tracked(hass)


async def test_feed_outage_does_not_count(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await start(hass, make())
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    hub.vehicles = []
    await advance(hass, freezer, RETENTION + timedelta(days=1))
    hub.vehicles = [other()]
    await advance(hass, freezer, timedelta(hours=1))
    assert TRACKER in tracked(hass)


async def test_sightings_survive_a_restart(
    hass: HomeAssistant,
    hub: FakeHub,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, object],
) -> None:
    entry = make()
    entry.add_to_hass(hass)
    unique_id = stored_tracker(hass, entry)
    day = timedelta(days=1).total_seconds()
    hass_storage[sightings_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": sightings_key(entry),
        "data": {"clock": 10 * day, "seen": {unique_id: 9 * day}},
    }
    hub.vehicles = [other()]
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The time Home Assistant was off before this start does not count.
    await advance(hass, freezer, RETENTION - timedelta(days=1, hours=1))
    assert TRACKER in tracked(hass)
    await advance(hass, freezer, timedelta(hours=2))
    assert TRACKER not in tracked(hass)


async def test_tracker_without_sighting_counts_as_seen_at_startup(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    entry = make()
    entry.add_to_hass(hass)
    stored_tracker(hass, entry)
    hub.vehicles = [other()]
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, RETENTION - timedelta(hours=1))
    assert TRACKER in tracked(hass)


async def test_sightings_are_deleted_with_the_entry(
    hass: HomeAssistant,
    hub: FakeHub,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, object],
) -> None:
    hub.vehicles = [vehicle()]
    entry = make()
    await start(hass, entry)
    await advance(hass, freezer, DEPARTURES_INTERVAL)
    await advance(hass, freezer, timedelta(seconds=5))
    assert sightings_key(entry) in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert sightings_key(entry) not in hass_storage


async def test_unreadable_sightings_count_as_seen_at_startup(
    hass: HomeAssistant,
    hub: FakeHub,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, object],
) -> None:
    entry = make()
    entry.add_to_hass(hass)
    unique_id = stored_tracker(hass, entry)
    hass_storage[sightings_key(entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": sightings_key(entry),
        "data": {unique_id: "2026-10-01T00:00:00+00:00"},
    }
    hub.vehicles = [other()]
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await advance(hass, freezer, RETENTION - timedelta(hours=1))
    assert TRACKER in tracked(hass)
