"""Vehicles on the map, behind their gate."""

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DEMO_VEHICLES_VARIABLE, DEPARTURES_INTERVAL
from custom_components.gtfs_hub.marker import STOP_MARKER_URL, form_of, marker_svg, marker_url
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import (
    FakeHub,
    departure,
    get_state,
    line_subentry,
    make_entry,
    stop_subentry,
    vehicle,
)
from gtfs_hub import VehicleClass, VehicleKey

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

TRACKER = "device_tracker.11_v1"


@pytest.fixture
def demo() -> Generator[None]:
    with patch.dict("os.environ", {DEMO_VEHICLES_VARIABLE: "1"}):
        yield


async def setup(hass: HomeAssistant, hub: FakeHub) -> MockConfigEntry:
    entry = make_entry(line_subentry(), stop_subentry())
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(DEPARTURES_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_no_trackers_without_the_gate(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.vehicles = [vehicle()]
    with patch.object(hub, "async_fetch_vehicles") as fetch:
        await setup(hass, hub)
    fetch.assert_not_called()
    assert hass.states.async_entity_ids("device_tracker") == []


@pytest.mark.usefixtures("demo")
async def test_vehicle_appears_on_the_map(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    await setup(hass, hub)
    hub.vehicles = [vehicle()]
    await tick(hass, freezer)
    state = get_state(hass, TRACKER)
    assert state.state == "not_home"
    assert state.attributes["latitude"] == 50.0
    assert state.attributes["line"] == "11"
    assert state.attributes["derived_bearing"] is None
    assert state.attributes["entity_picture"] == "/api/gtfs_hub/marker/3/standard/none/11"


@pytest.mark.usefixtures("demo")
async def test_new_trip_keeps_the_entity_and_absence_is_unavailable(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass, hub)
    await tick(hass, freezer)
    hub.vehicles = [vehicle("t2")]
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).attributes["trip_id"] == "t2"
    trackers = [e for e in er.async_get(hass).entities if e.startswith("device_tracker.")]
    assert trackers == [TRACKER]
    for _ in range(5):
        hub.vehicles = []
        await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("demo")
async def test_direction_needs_real_movement(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass, hub)
    await tick(hass, freezer)
    hub.vehicles = [vehicle(latitude=50.00005)]
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).attributes["derived_bearing"] is None
    hub.vehicles = [vehicle(latitude=50.001)]
    await tick(hass, freezer)
    state = get_state(hass, TRACKER)
    assert state.attributes["derived_bearing"] == pytest.approx(0, abs=1)
    assert state.attributes["entity_picture"] == "/api/gtfs_hub/marker/3/standard/0/11"


@pytest.mark.usefixtures("demo")
async def test_unchanged_position_is_not_written(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass, hub)
    await tick(hass, freezer)
    before = get_state(hass, TRACKER).last_updated
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).last_updated == before


async def test_pinned_stop_has_coordinates(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures[departure().stop_key] = [departure()]
    await setup(hass, hub)
    state = get_state(hass, "sensor.alfa_next_departure")
    assert (state.attributes["latitude"], state.attributes["longitude"]) == (50.0, 19.0)


def test_marker_urls_round_the_bearing() -> None:
    assert marker_url(3, "long", "11", None) == "/api/gtfs_hub/marker/3/long/none/11"
    assert marker_url(3, "long", "11", 97.0) == "/api/gtfs_hub/marker/3/long/90/11"
    assert marker_url(3, "long", "11", 359.0) == "/api/gtfs_hub/marker/3/long/0/11"


@pytest.mark.parametrize(
    ("planned", "form"),
    [
        (None, "standard"),
        (VehicleClass("x"), "standard"),
        (VehicleClass("M N", 8, 1, True), "short-low"),
        (VehicleClass("B N", 12, 1, True), "standard-low"),
        (VehicleClass("C N", 18, 1, False), "long"),
        (VehicleClass("2x105HF", None, 2, False), "double"),
    ],
)
def test_form_of(planned: VehicleClass | None, form: str) -> None:
    assert form_of(planned) == form


def test_marker_draws_line_arrow_and_ring() -> None:
    assert "rotate(90 50 50)" in marker_svg(3, "standard", "11", 90)
    assert "rotate" not in marker_svg(3, "standard", "11", None)
    assert "&lt;" in marker_svg(3, "standard", "<x", None)
    assert 'r="45"' in marker_svg(3, "standard-low", "11", None)
    assert 'r="45"' not in marker_svg(3, "standard", "11", None)


@pytest.mark.parametrize("route_type", [3, 0, 11])
@pytest.mark.parametrize("form", ["short", "standard", "long", "double"])
def test_every_picture_differs_by_form_and_mode(route_type: int, form: str) -> None:
    pictures = {
        marker_svg(route_type, name, "1", None) for name in ("short", "standard", "long", "double")
    }
    assert len(pictures) == 4
    assert marker_svg(route_type, form, "1", None) != marker_svg(
        (route_type + 3) % 14, form, "1", None
    )


@pytest.mark.usefixtures("demo")
async def test_planned_class_in_attributes(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [replace(vehicle(), planned_class=VehicleClass("C N", 18, 1, True))]
    await setup(hass, hub)
    await tick(hass, freezer)
    state = get_state(hass, TRACKER)
    assert state.attributes["planned_vehicle"] == "C N"
    assert state.attributes["length"] == 18
    assert state.attributes["low_floor"] is True
    assert "/long-low/" in state.attributes["entity_picture"]


async def test_marker_view(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator, hub: FakeHub
) -> None:
    await async_setup_component(hass, "http", {})
    await setup(hass, hub)
    client = await hass_client_no_auth()
    response = await client.get("/api/gtfs_hub/marker/3/long-low/90/11")
    assert response.status == 200
    assert response.content_type == "image/svg+xml"
    assert (await client.get("/api/gtfs_hub/marker/3/long/bad/11")).status == 404


@pytest.mark.usefixtures("demo")
async def test_vehicle_returns_at_the_same_place(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass, hub)
    await tick(hass, freezer)
    for _ in range(5):
        hub.vehicles = []
        await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == STATE_UNAVAILABLE
    hub.vehicles = [vehicle()]
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == "not_home"


def test_marker_url_quotes_the_line() -> None:
    assert marker_url(3, "long", "T/6", None) == "/api/gtfs_hub/marker/3/long/none/T%2F6"


async def test_pinned_stop_has_a_marker(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator, hub: FakeHub
) -> None:
    await async_setup_component(hass, "http", {})
    await setup(hass, hub)
    picture = get_state(hass, "sensor.alfa_next_departure").attributes["entity_picture"]
    assert picture == STOP_MARKER_URL
    client = await hass_client_no_auth()
    response = await client.get(picture)
    assert response.status == 200
    assert response.content_type == "image/svg+xml"


@pytest.mark.usefixtures("demo")
async def test_empty_feed_keeps_vehicles_for_a_moment(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass, hub)
    await tick(hass, freezer)
    hub.vehicles = []
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == "not_home"
    for _ in range(5):
        await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("demo")
async def test_one_vehicle_leaving_goes_at_once(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    other = replace(vehicle(), key=VehicleKey("v2"))
    hub.vehicles = [vehicle(), other]
    await setup(hass, hub)
    await tick(hass, freezer)
    hub.vehicles = [other]
    await tick(hass, freezer)
    assert get_state(hass, TRACKER).state == STATE_UNAVAILABLE
