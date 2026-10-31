from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub import database_path
from custom_components.gtfs_hub.const import (
    DEPARTURES_INTERVAL,
    DOMAIN,
    SCHEDULE_INTERVAL,
    SCHEDULE_RETRY_INTERVAL,
)
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import (
    NOW,
    STOP_A,
    FakeHub,
    departure,
    get_state,
    line_subentry,
    make_entry,
    stop_subentry,
)
from gtfs_hub import DepartureStatus, GtfsConnectionError, GtfsNoDataError, RealtimeData

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from syrupy.assertion import SnapshotAssertion

NEXT = "sensor.alfa_next_departure"
DELAY = "sensor.alfa_delay"
LIVE = "binary_sensor.fake_live_data"


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


async def setup(
    hass: HomeAssistant,
    *subentries: ConfigSubentryData,
    options: dict[str, list[str]] | None = None,
) -> MockConfigEntry:
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
    hub.departures[STOP_A.key] = [departure(delay=120, status=DepartureStatus.PREDICTED)]
    await setup(hass, line_subentry(), stop_subentry())
    state = get_state(hass, NEXT)
    assert state.state == "2026-11-02T12:07:00+00:00"
    assert state.attributes["line"] == "11"
    assert state.attributes["realtime"] is True
    assert state.attributes["timetable_url"] == "https://example.test/a"
    assert state.attributes["attribution"] == "Data: Fake"
    assert state.attributes["departures"][0]["status"] == "predicted"
    assert float(get_state(hass, DELAY).state) == 120


async def test_no_prediction_is_unknown_delay(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures[STOP_A.key] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert get_state(hass, NEXT).state == "2026-11-02T12:05:00+00:00"
    assert get_state(hass, NEXT).attributes["realtime"] is False
    assert get_state(hass, DELAY).state == STATE_UNKNOWN


async def test_no_departures_stays_available(hass: HomeAssistant, hub: FakeHub) -> None:
    await setup(hass, line_subentry(), stop_subentry())
    assert get_state(hass, NEXT).state == STATE_UNKNOWN


async def test_cancelled_is_listed_but_not_next(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures[STOP_A.key] = [
        departure(1, status=DepartureStatus.CANCELLED),
        departure(2, status=DepartureStatus.SKIPPED),
        departure(3),
    ]
    await setup(hass, line_subentry(), stop_subentry())
    state = get_state(hass, NEXT)
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
    hub.departures[STOP_A.key] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert get_state(hass, LIVE).state == STATE_ON
    hub.realtime_error = GtfsConnectionError("down")
    await tick(hass, freezer)
    assert get_state(hass, LIVE).state == STATE_OFF
    assert get_state(hass, NEXT).state != STATE_UNAVAILABLE
    hub.realtime_error = None
    await tick(hass, freezer)
    assert get_state(hass, LIVE).state == STATE_ON


async def test_stale_feed_turns_live_data_off(hass: HomeAssistant, hub: FakeHub) -> None:
    from datetime import timedelta

    hub.realtime = RealtimeData(NOW - timedelta(minutes=6))
    await setup(hass, line_subentry(), stop_subentry())
    assert get_state(hass, LIVE).state == STATE_OFF


async def test_no_schedule_yet_means_unavailable(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.ready = False
    hub.sync_error = GtfsConnectionError("down")
    entry = await setup(hass, line_subentry(), stop_subentry())
    assert entry.state is ConfigEntryState.LOADED
    assert get_state(hass, NEXT).state == STATE_UNAVAILABLE


async def test_sync_failure_keeps_stored_data(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.sync_error = GtfsConnectionError("down")
    hub.departures[STOP_A.key] = [departure()]
    await setup(hass, line_subentry(), stop_subentry())
    assert get_state(hass, NEXT).state == "2026-11-02T12:05:00+00:00"


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
    hub.departures[STOP_A.key] = [departure()]
    await setup(hass, line_subentry(), options={"persons": ["person.ania"]})
    state = get_state(hass, "sensor.fake_nearest_stop_ania")
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
    assert get_state(hass, "sensor.fake_nearest_stop_ania").state == STATE_UNKNOWN
    person(hass, None, None)
    await tick(hass, freezer)
    state = get_state(hass, "sensor.fake_nearest_stop_ania")
    assert state.state == STATE_UNKNOWN
    assert state.attributes["departures"] == []


async def test_action_known_and_unknown_stop(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures[STOP_A.key] = [departure()]
    entry = await setup(hass, line_subentry())
    response = await hass.services.async_call(
        DOMAIN,
        "get_departures",
        {"config_entry_id": entry.entry_id, "stop_key": "A"},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    departures = response["departures"]
    assert isinstance(departures, list)
    assert isinstance(departures[0], dict)
    assert departures[0]["line"] == "11"
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
    path = database_path(hass, "fake")
    await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
    await hass.async_add_executor_job(path.write_bytes, b"x")
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not await hass.async_add_executor_job(path.exists)


async def test_no_realtime_without_pins_or_persons(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.realtime = RealtimeData(NOW)
    with patch.object(hub, "async_fetch_realtime") as fetch:
        await setup(hass, line_subentry())
    fetch.assert_not_called()
    assert get_state(hass, LIVE).state == STATE_OFF


async def test_failed_sync_retries_soon(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.sync_error = GtfsConnectionError("down")
    entry = await setup(hass, line_subentry())
    assert entry.runtime_data.schedule.update_interval == SCHEDULE_RETRY_INTERVAL
    hub.sync_error = None
    await entry.runtime_data.schedule.async_refresh()
    assert entry.runtime_data.schedule.update_interval == SCHEDULE_INTERVAL


async def test_removed_person_loses_the_sensor(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await setup(hass, line_subentry(), options={"persons": ["person.ania"]})
    entity_id = "sensor.fake_nearest_stop_ania"
    assert hass.states.get(entity_id) is not None
    hass.config_entries.async_update_entry(entry, options={"persons": []})
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(entity_id) is None


async def test_action_fetches_realtime_when_nothing_polls(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    hub.realtime = RealtimeData(NOW)
    entry = await setup(hass, line_subentry())
    with patch.object(hub, "async_fetch_realtime", return_value=hub.realtime) as fetch:
        await hass.services.async_call(
            DOMAIN,
            "get_departures",
            {"config_entry_id": entry.entry_id, "stop_key": "A"},
            blocking=True,
            return_response=True,
        )
    fetch.assert_called_once()


async def test_failed_sync_without_lines_or_database_is_a_failure(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    hub.ready = False
    hub.sync_error = GtfsConnectionError("down")
    entry = await setup(hass)
    assert entry.runtime_data.schedule.last_update_success is False
    assert entry.runtime_data.schedule.update_interval == SCHEDULE_RETRY_INTERVAL


async def test_unavailable_provider_fails_setup(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.gtfs_hub.async_create_provider", side_effect=GtfsNoDataError("gone")
    ):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_schedule_gone_makes_entities_unavailable(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    await setup(hass, line_subentry(), stop_subentry())
    with patch.object(hub, "async_departures", side_effect=GtfsNoDataError("gone")):
        await tick(hass, freezer)
    assert get_state(hass, NEXT).state == STATE_UNAVAILABLE


async def test_entity_snapshot(
    hass: HomeAssistant, hub: FakeHub, snapshot: SnapshotAssertion
) -> None:
    hub.departures[STOP_A.key] = [
        departure(delay=120, status=DepartureStatus.PREDICTED),
        departure(9, status=DepartureStatus.CANCELLED),
    ]
    hub.realtime = RealtimeData(NOW)
    await setup(hass, line_subentry(), stop_subentry())
    for entity_id in (NEXT, DELAY, LIVE):
        state = get_state(hass, entity_id)
        assert (state.state, dict(state.attributes)) == snapshot(name=entity_id)
