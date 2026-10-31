from pathlib import Path
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DEPARTURES_INTERVAL, DOMAIN
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from conftest import NOW, FakeHub, departure, line_subentry, make_entry, stop_subentry
from gtfs_hub import DepartureStatus, GtfsConnectionError, GtfsNoDataError, RealtimeData

NEXT = "sensor.alfa_next_departure"
DELAY = "sensor.alfa_delay"
LIVE = "binary_sensor.fake_live_data"


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def setup(hass: HomeAssistant, *subentries, options=None):
    entry = make_entry(*subentries, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(DEPARTURES_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_prediction(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures["A"] = [departure(delay=120, status=DepartureStatus.PREDICTED)]
    await setup(hass, line_subentry(), stop_subentry())
    state = hass.states.get(NEXT)
    assert state.state == "2026-11-02T12:07:00+00:00"
    assert state.attributes["line"] == "11"
    assert state.attributes["realtime"] is True
    assert state.attributes["timetable_url"] == "https://example.test/a"
    assert state.attributes["attribution"] == "Data: Fake"
    assert state.attributes["departures"][0]["status"] == "predicted"
    assert float(hass.states.get(DELAY).state) == 120


async def test_no_prediction_is_unknown_delay(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures["A"] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert hass.states.get(NEXT).state == "2026-11-02T12:05:00+00:00"
    assert hass.states.get(NEXT).attributes["realtime"] is False
    assert hass.states.get(DELAY).state == STATE_UNKNOWN


async def test_no_departures_stays_available(hass: HomeAssistant, hub: FakeHub) -> None:
    await setup(hass, line_subentry(), stop_subentry())
    assert hass.states.get(NEXT).state == STATE_UNKNOWN


async def test_cancelled_is_listed_but_not_next(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures["A"] = [
        departure(1, status=DepartureStatus.CANCELLED),
        departure(2, status=DepartureStatus.SKIPPED),
        departure(3),
    ]
    await setup(hass, line_subentry(), stop_subentry())
    state = hass.states.get(NEXT)
    assert state.state == "2026-11-02T12:03:00+00:00"
    assert len(state.attributes["departures"]) == 3


async def test_departures_are_not_recorded(hass: HomeAssistant, hub: FakeHub) -> None:
    await setup(hass, line_subentry(), stop_subentry())
    from custom_components.gtfs_hub.sensor import NearestStopSensor, NextDepartureSensor

    assert "departures" in NextDepartureSensor._unrecorded_attributes
    assert "departures" in NearestStopSensor._unrecorded_attributes


async def test_realtime_failure_keeps_the_schedule(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.realtime = RealtimeData(NOW)
    hub.departures["A"] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert hass.states.get(LIVE).state == STATE_ON
    hub.realtime_error = GtfsConnectionError("down")
    await tick(hass, freezer)
    assert hass.states.get(LIVE).state == STATE_OFF
    assert hass.states.get(NEXT).state != STATE_UNAVAILABLE
    hub.realtime_error = None
    await tick(hass, freezer)
    assert hass.states.get(LIVE).state == STATE_ON


async def test_stale_feed_turns_live_data_off(hass: HomeAssistant, hub: FakeHub) -> None:
    from datetime import timedelta

    hub.realtime = RealtimeData(NOW - timedelta(minutes=6))
    await setup(hass, line_subentry(), stop_subentry())
    assert hass.states.get(LIVE).state == STATE_OFF


async def test_no_schedule_yet_means_unavailable(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.ready = False
    hub.sync_error = GtfsConnectionError("down")
    entry = await setup(hass, line_subentry(), stop_subentry())
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(NEXT).state == STATE_UNAVAILABLE


async def test_sync_failure_keeps_stored_data(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.sync_error = GtfsConnectionError("down")
    hub.departures["A"] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert hass.states.get(NEXT).state == "2026-11-02T12:05:00+00:00"


async def test_selection_reaches_the_library(hass: HomeAssistant, hub: FakeHub) -> None:
    await setup(hass, line_subentry())
    assert hub.synced[0][0].short_name == "11"


async def test_setup_does_not_wait_for_sync(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make_entry(line_subentry(), stop_subentry())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    await hass.async_block_till_done()


def person(hass: HomeAssistant, latitude: float | None, longitude: float | None) -> None:
    attributes = {"latitude": latitude, "longitude": longitude} if latitude else {}
    hass.states.async_set("person.ania", "not_home", attributes)


async def test_nearest_stop(hass: HomeAssistant, hub: FakeHub) -> None:
    person(hass, 50.0009, 19.0)
    hub.departures["A"] = [departure()]
    await setup(hass, line_subentry(), options={"persons": ["person.ania"]})
    state = hass.states.get("sensor.fake_nearest_stop_ania")
    assert state.state == "Alfa"
    assert state.attributes["stop_code"] == "C-A"
    assert state.attributes["distance"] == 100
    assert state.attributes["timetable_url"] == "https://example.test/a"
    assert len(state.attributes["departures"]) == 1


async def test_nearest_stop_far_or_unknown(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    person(hass, 52.0, 21.0)
    await setup(hass, line_subentry(), options={"persons": ["person.ania"]})
    assert hass.states.get("sensor.fake_nearest_stop_ania").state == STATE_UNKNOWN
    person(hass, None, None)
    await tick(hass, freezer)
    state = hass.states.get("sensor.fake_nearest_stop_ania")
    assert state.state == STATE_UNKNOWN
    assert state.attributes["departures"] == []


async def test_action_known_and_unknown_stop(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures["A"] = [departure()]
    entry = await setup(hass, line_subentry())
    response = await hass.services.async_call(
        DOMAIN,
        "get_departures",
        {"config_entry_id": entry.entry_id, "stop_key": "A"},
        blocking=True,
        return_response=True,
    )
    assert response["departures"][0]["line"] == "11"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_departures",
            {"config_entry_id": entry.entry_id, "stop_key": "nope"},
            blocking=True,
            return_response=True,
        )


async def test_action_without_schedule(hass: HomeAssistant, hub: FakeHub) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entry = await setup(hass, line_subentry())
    with (
        patch.object(hub, "async_departures", side_effect=GtfsNoDataError("x")),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            DOMAIN,
            "get_departures",
            {"config_entry_id": entry.entry_id, "stop_key": "A"},
            blocking=True,
            return_response=True,
        )


async def test_cache_removed_with_the_entry(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await setup(hass, line_subentry())
    path = Path(hass.config.cache_path(DOMAIN, f"{entry.entry_id}.db"))
    await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
    await hass.async_add_executor_job(path.write_bytes, b"x")
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not await hass.async_add_executor_job(path.exists)
