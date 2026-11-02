"""Delay event entities of tracked lines."""

from typing import TYPE_CHECKING

from custom_components.gtfs_hub.const import DEPARTURES_INTERVAL, DOMAIN
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import LINE_11, TRAM_11, FakeHub, get_state, line_subentry, make_entry
from gtfs_hub import LineKey, TripDelay, TripId

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

EVENT = "event.bus_11_delays"


def delay(seconds: int | None, line: LineKey = LINE_11) -> TripDelay:
    return TripDelay(TripId("t1"), None, line, "Terminus", seconds, False)


async def start(
    hass: HomeAssistant, options: dict[str, list[str] | int] | None = None
) -> MockConfigEntry:
    entry = make_entry(line_subentry(), options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(DEPARTURES_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_line_has_an_event_entity_on_its_device(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await start(hass)
    item = er.async_get(hass).async_get(EVENT)
    assert item is not None
    assert item.config_subentry_id is not None
    assert item.unique_id == f"{item.config_subentry_id}_delays"
    state = get_state(hass, EVENT)
    assert state.attributes["event_types"] == ["delayed", "recovered", "cancelled"]
    assert entry.entry_id == item.config_entry_id
    assert item.device_id is not None


async def test_delay_fires_an_event(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.trip_delays = [delay(0)]
    await start(hass)
    await tick(hass, freezer)
    hub.trip_delays = [delay(300)]
    for _ in range(2):
        await tick(hass, freezer)
    state = get_state(hass, EVENT)
    assert state.attributes["event_type"] == "delayed"
    assert state.attributes["delay"] == 300
    assert state.attributes["line"] == "11"
    assert state.attributes["trip_id"] == "t1"
    fired = state.state
    # A repeated listener call with the same tick fires nothing new.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    entry.runtime_data.departures.async_update_listeners()
    await hass.async_block_till_done()
    assert get_state(hass, EVENT).state == fired


async def test_other_lines_do_not_fire(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.trip_delays = [delay(0, TRAM_11)]
    await start(hass)
    await tick(hass, freezer)
    hub.trip_delays = [delay(300, TRAM_11)]
    for _ in range(2):
        await tick(hass, freezer)
    assert get_state(hass, EVENT).attributes["event_type"] is None


async def test_lines_without_pinned_stops_fetch_live_data(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.trip_delays = []
    await start(hass)
    before = hub.realtime_fetches
    await tick(hass, freezer)
    assert hub.realtime_fetches > before


async def test_threshold_comes_from_the_options(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.trip_delays = [delay(0)]
    await start(hass, {"delay_threshold": 10})
    await tick(hass, freezer)
    hub.trip_delays = [delay(300)]
    for _ in range(2):
        await tick(hass, freezer)
    assert get_state(hass, EVENT).attributes["event_type"] is None
