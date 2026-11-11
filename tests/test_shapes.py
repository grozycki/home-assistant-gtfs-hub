"""Directions from shapes, and the action that returns them."""

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import pytest
from custom_components.gtfs_hub.const import DEMO_VEHICLES_VARIABLE, DEPARTURES_INTERVAL, DOMAIN
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from conftest import LINE_11, FakeHub, get_state, line_subentry, make_entry, vehicle
from gtfs_hub import Livery, Shape, ShapeId, VehicleKey

if TYPE_CHECKING:
    from collections.abc import Generator

    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

TRACKER = "device_tracker.gtfs_hub_fake_11_v1"
# East along a street, about 70 m between points.
EAST = Shape(
    ShapeId("east"),
    LINE_11,
    "Terminus",
    0,
    tuple((50.0, 19.0 + step * 0.001) for step in range(6)),
)
# East along a street, round a block to the north, then west on the same street.
OUT_AND_BACK = Shape(
    ShapeId("loop"),
    LINE_11,
    "Terminus",
    0,
    (
        *EAST.points,
        (50.002, 19.005),
        (50.002, 19.007),
        (50.0, 19.007),
        (50.0, 19.006),
        *EAST.points[::-1],
    ),
)


@pytest.fixture(autouse=True)
def demo() -> Generator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(DEMO_VEHICLES_VARIABLE, "1")
        yield


async def setup(hass: HomeAssistant, *subentries: ConfigSubentryData) -> MockConfigEntry:
    entry = make_entry(*(subentries or (line_subentry(),)))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(DEPARTURES_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def bearing_of(hass: HomeAssistant) -> object:
    return get_state(hass, TRACKER).attributes["derived_bearing"]


async def test_arrow_at_first_sight_and_while_standing(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle(latitude=50.0001, longitude=19.0025, shape=EAST)]
    await setup(hass)
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(90, abs=1)
    assert "/marker/3/standard/90/11" in get_state(hass, TRACKER).attributes["entity_picture"]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(90, abs=1)


async def test_far_from_the_shape_falls_back_to_movement(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle(latitude=50.01, longitude=19.0, shape=EAST)]
    await setup(hass)
    await tick(hass, freezer)
    assert bearing_of(hass) is None
    hub.vehicles = [vehicle(latitude=50.011, longitude=19.0, shape=EAST)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(0, abs=1)


async def test_way_back_follows_the_previous_location(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    # Ambiguous at first sight, located once the vehicle is seen round the block.
    hub.vehicles = [vehicle(latitude=50.0, longitude=19.0025, shape=OUT_AND_BACK)]
    await setup(hass)
    await tick(hass, freezer)
    assert bearing_of(hass) is None
    hub.vehicles = [vehicle(latitude=50.002, longitude=19.006, shape=OUT_AND_BACK)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(90, abs=1)
    hub.vehicles = [vehicle(latitude=50.0, longitude=19.0045, shape=OUT_AND_BACK)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(270, abs=1)
    hub.vehicles = [vehicle(latitude=50.0, longitude=19.0025, shape=OUT_AND_BACK)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(270, abs=1)


async def test_new_trip_forgets_the_previous_location(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle(latitude=50.002, longitude=19.006, shape=OUT_AND_BACK)]
    await setup(hass)
    await tick(hass, freezer)
    hub.vehicles = [vehicle(latitude=50.0, longitude=19.0045, shape=OUT_AND_BACK)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(270, abs=1)
    # Located from the old trip, this would be the way back. On a new trip it is ambiguous,
    # so the movement east decides.
    hub.vehicles = [vehicle(trip="t2", latitude=50.0, longitude=19.0049, shape=OUT_AND_BACK)]
    await tick(hass, freezer)
    assert bearing_of(hass) == pytest.approx(90, abs=1)


async def call(hass: HomeAssistant, entry: MockConfigEntry, **data: object) -> object:
    response = await hass.services.async_call(
        DOMAIN,
        "get_line_shapes",
        {"config_entry_id": entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    return response["shapes"]


async def test_action_returns_the_paths(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.shapes[LINE_11] = [EAST]
    entry = await setup(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_line_shapes",
        {"config_entry_id": entry.entry_id, "line": "11"},
        blocking=True,
        return_response=True,
    )
    assert response == {
        "provider": "fake",
        "line": "11",
        "route_type": 3,
        "line_color": "#1565C0",
        "line_text_color": "#FFFFFF",
        "path_color": "#1565C0",
        "shapes": [
            {
                "id": "east",
                "headsign": "Terminus",
                "direction_id": 0,
                "color": None,
                "points": [[latitude, longitude] for latitude, longitude in EAST.points],
            }
        ],
    }


async def test_trackers_name_their_source_and_mode(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle()]
    await setup(hass)
    await tick(hass, freezer)
    attributes = get_state(hass, TRACKER).attributes
    assert (attributes["route_type"], attributes["provider"]) == (3, "fake")


async def test_action_refuses_an_unselected_line(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await setup(hass)
    with pytest.raises(ServiceValidationError) as raised:
        await call(hass, entry, line="12")
    assert raised.value.translation_key == "unknown_line"


async def test_action_needs_a_route_type_for_a_shared_name(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    tram = ConfigSubentryData(
        data={"route_type": 0, "short_name": "11"},
        subentry_type="line",
        title="Tram 11",
        unique_id="0:11",
    )
    entry = await setup(hass, line_subentry(), tram)
    with pytest.raises(ServiceValidationError) as raised:
        await call(hass, entry, line="11")
    assert raised.value.translation_key == "ambiguous_line"
    hub.shapes[LINE_11] = [EAST]
    shapes = await call(hass, entry, line="11", route_type=3)
    assert isinstance(shapes, list)
    assert len(shapes) == 1


async def test_action_without_schedule(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await setup(hass)
    hub.ready = False
    with pytest.raises(HomeAssistantError):
        await call(hass, entry, line="11")


async def test_action_asks_for_todays_paths_and_cleans_the_colour(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-03 10:00:00+00:00")
    hub.shapes[LINE_11] = [EAST]
    entry = await setup(hass)
    runtime = entry.runtime_data
    runtime.schedule.data.lines[LINE_11] = replace(
        runtime.schedule.data.lines[LINE_11], color="#FF0000"
    )
    shapes = await call(hass, entry, line="11")
    assert isinstance(shapes, list)
    assert shapes[0]["color"] == "#FF0000"
    assert hub.shape_dates == [date(2026, 11, 3)]


async def test_headings_of_vehicles_gone_from_the_feed_are_dropped(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    hub.vehicles = [vehicle(latitude=50.0001, longitude=19.0025, shape=EAST)]
    entry = await setup(hass)
    await tick(hass, freezer)
    other = replace(vehicle(), key=VehicleKey("v2"))
    hub.vehicles = [other]
    await tick(hass, freezer)
    assert list(entry.runtime_data.departures._headings) == ["3:11:v2"]


async def test_livery_paths_take_the_body_colour(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.provider.livery = Livery(body="#FFD200", trim="#1A1A1A")
    entry = await setup(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_line_shapes",
        {"config_entry_id": entry.entry_id, "line": "11"},
        blocking=True,
        return_response=True,
    )
    assert response is not None
    assert (response["line_color"], response["path_color"]) == ("#1A1A1A", "#FFD200")
