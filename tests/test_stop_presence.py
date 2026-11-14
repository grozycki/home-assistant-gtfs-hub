"""Stop presence triggers and condition, run inside automations."""

from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from custom_components.gtfs_hub.const import DOMAIN
from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import parse_yaml
from pytest_homeassistant.common import async_fire_time_changed, async_mock_service

from conftest import NOW, STOP_A, FakeHub, departure, line_subentry, make_entry, stop_subentry
from gtfs_hub import Stop, StopKey

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant, ServiceCall
    from pytest_homeassistant.common import MockConfigEntry

PHONE = "device_tracker.phone"
TABLET = "device_tracker.tablet"
# At this latitude, 0.001 degree of latitude is about 111 m.
METRE = 1 / 111_195
# The other pole of stop A, across the street, and a stop down the road.
# Stop A stands at 50.0, 19.0.
POLE = Stop(StopKey("P"), "Alfa 2", None, 50.0 + 45 * METRE, 19.0)
NEAR = Stop(StopKey("N"), "Near", None, 50.0 + 400 * METRE, 19.0)


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(NOW)


def place(hass: HomeAssistant, entity_id: str, north: float | None, accuracy: float = 5) -> None:
    """Put a tracker this many metres north of stop A, or nowhere."""
    if north is None:
        hass.states.async_set(entity_id, "not_home", {"source_type": "router"})
        return
    attributes = {
        "latitude": STOP_A.latitude + north * METRE if STOP_A.latitude else 0,
        "longitude": STOP_A.longitude,
        "gps_accuracy": accuracy,
        "source_type": "gps",
    }
    hass.states.async_set(entity_id, "not_home", attributes)


async def moved(hass: HomeAssistant, entity_id: str, north: float | None, **kw: float) -> None:
    place(hass, entity_id, north, **kw)
    await hass.async_block_till_done()


async def setup_entry(hass: HomeAssistant, *stops: Stop) -> MockConfigEntry:
    """Set up an entry with stop A pinned, or with these stops pinned instead."""
    subentries = [pin(stop) for stop in stops] if stops else [stop_subentry()]
    entry = make_entry(line_subentry(), *subentries)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def pin(stop: Stop) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={"stop_key": stop.key}, subentry_type="stop", title=stop.name, unique_id=stop.key
    )


def subentry_id(entry: MockConfigEntry, key: str = "A") -> str:
    return str(
        next(
            subentry.subentry_id
            for subentry in entry.get_subentries_of_type("stop")
            if subentry.data["stop_key"] == key
        )
    )


def stop_device(hass: HomeAssistant, entry: MockConfigEntry, key: str = "A") -> str:
    stop_id = subentry_id(entry, key)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, stop_id), entry.entry_id)
    assert device is not None
    return device.id


async def automation(
    hass: HomeAssistant,
    trigger: str,
    device_id: str | list[str] | None,
    targets: list[str] | None = None,
    options: dict[str, object] | None = None,
) -> list[ServiceCall]:
    """Set up an automation on a trigger, with no stop field when device_id is None."""
    calls: list[ServiceCall] = async_mock_service(hass, "test", "automation")
    stop = {} if device_id is None else {"stop": device_id}
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "triggers": {
                    "trigger": f"{DOMAIN}.{trigger}",
                    "target": {"entity_id": targets or [PHONE]},
                    "options": {**stop, **(options or {})},
                },
                "actions": {
                    "action": "test.automation",
                    "data": {
                        "entity_id": "{{ trigger.entity_id }}",
                        "stop": "{{ trigger.stop.name }}",
                        "code": "{{ trigger.stop.code }}",
                        "key": "{{ trigger.stop.key }}",
                        "distance": "{{ trigger.distance }}",
                        "lines": "{{ trigger.departures | map(attribute='line') | list }}",
                    },
                },
            }
        },
    )
    await hass.async_block_till_done()
    return calls


async def test_entering_fires_once(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.departures[STOP_A.key] = [departure(), departure(minutes=9)]
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 30)
    await moved(hass, PHONE, 20)
    assert len(calls) == 1
    data = calls[0].data
    assert data["entity_id"] == PHONE
    assert data["stop"] == "Alfa"
    assert data["code"] == "C-A"
    assert data["key"] == "A"
    assert data["distance"] == 30
    assert data["lines"] == ["11", "11"]


async def test_departures_empty_before_the_first_update(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 30)
    assert calls[0].data["lines"] == []


async def test_leaving_needs_the_margin(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 20)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_left_stop", stop_device(hass, entry))
    for north in (55, 45, 60):
        await moved(hass, PHONE, north)
    assert calls == []
    await moved(hass, PHONE, 150)
    await moved(hass, PHONE, 300)
    assert len(calls) == 1
    assert calls[0].data["distance"] == 150


async def test_jitter_does_not_fire_entering_again(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    for north in (40, 55, 45, 60):
        await moved(hass, PHONE, north)
    assert len(calls) == 1


async def test_restart_at_the_stop_fires_nothing(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 10)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 20)
    assert calls == []


async def test_tracker_appearing_at_the_stop_fires_nothing(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 10)
    await moved(hass, PHONE, 20)
    assert calls == []


async def test_duration_skips_a_pass_by(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass, "person_entered_stop", stop_device(hass, entry), options={"for": {"seconds": 30}}
    )
    await moved(hass, PHONE, 30)
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await moved(hass, PHONE, 300)
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == []


async def test_duration_fires_after_staying(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass, "person_entered_stop", stop_device(hass, entry), options={"for": {"seconds": 30}}
    )
    await moved(hass, PHONE, 30)
    await moved(hass, PHONE, 60)
    assert calls == []
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_each_target_fires_alone(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    place(hass, TABLET, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass, "person_entered_stop", stop_device(hass, entry), targets=[PHONE, TABLET]
    )
    await moved(hass, TABLET, 10)
    assert [call.data["entity_id"] for call in calls] == [TABLET]
    await moved(hass, PHONE, 10)
    assert [call.data["entity_id"] for call in calls] == [TABLET, PHONE]


async def test_all_waits_for_every_target(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    place(hass, TABLET, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass,
        "person_entered_stop",
        stop_device(hass, entry),
        targets=[PHONE, TABLET],
        options={"behavior": "all"},
    )
    await moved(hass, TABLET, 10)
    assert calls == []
    await moved(hass, PHONE, 10)
    assert [call.data["entity_id"] for call in calls] == [PHONE]


async def test_first_fires_for_the_first_target(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    place(hass, TABLET, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass,
        "person_entered_stop",
        stop_device(hass, entry),
        targets=[PHONE, TABLET],
        options={"behavior": "first"},
    )
    await moved(hass, TABLET, 10)
    await moved(hass, PHONE, 10)
    assert [call.data["entity_id"] for call in calls] == [TABLET]


async def test_coarse_fix_and_no_coordinates_change_nothing(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 20, accuracy=200)
    await moved(hass, PHONE, None)
    assert calls == []


async def test_radius_option(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(
        hass, "person_entered_stop", stop_device(hass, entry), options={"radius": 150}
    )
    await moved(hass, PHONE, 120)
    assert len(calls) == 1


async def test_removed_stop_keeps_the_automation(
    hass: HomeAssistant, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    stop_id = entry.get_subentries_of_type("stop")[0].subentry_id
    hass.config_entries.async_remove_subentry(entry, stop_id)
    await hass.async_block_till_done()
    await moved(hass, PHONE, 10)
    await moved(hass, PHONE, 300)
    await moved(hass, PHONE, 10)
    assert calls == []
    assert hass.states.get("automation.automation_0") is not None
    assert caplog.text.count("is not a stop of GTFS Hub") == 1


async def test_not_synced_yet_fires_nothing_quietly(
    hass: HomeAssistant, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    device_id = stop_device(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    calls = await automation(hass, "person_entered_stop", device_id)
    await moved(hass, PHONE, 10)
    assert calls == []
    assert "is not a stop of GTFS Hub" not in caplog.text


async def test_before_the_first_sync_fires_nothing_quietly(
    hass: HomeAssistant, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    hub.ready = False
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 10)
    assert calls == []
    assert "is not a stop of GTFS Hub" not in caplog.text


async def at_stop_automation(
    hass: HomeAssistant,
    device_id: str | list[str] | None,
    targets: list[str] | None = None,
    options: dict[str, object] | None = None,
) -> list[ServiceCall]:
    """Run an automation on an event, passing only when the condition holds."""
    calls: list[ServiceCall] = async_mock_service(hass, "test", "automation")
    stop = {} if device_id is None else {"stop": device_id}
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "triggers": {"trigger": "event", "event_type": "check"},
                "conditions": {
                    "condition": f"{DOMAIN}.person_at_stop",
                    "target": {"entity_id": targets or [PHONE]},
                    "options": {**stop, **(options or {})},
                },
                "actions": {"action": "test.automation"},
            }
        },
    )
    await hass.async_block_till_done()
    return calls


async def check(hass: HomeAssistant, calls: list[ServiceCall]) -> bool:
    before = len(calls)
    hass.bus.async_fire("check")
    await hass.async_block_till_done()
    return len(calls) > before


async def test_condition_within_the_radius(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 20)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(hass, stop_device(hass, entry))
    assert await check(hass, calls)
    await moved(hass, PHONE, 80)
    assert not await check(hass, calls)


async def test_condition_radius_option(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 80)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(hass, stop_device(hass, entry), options={"radius": 100})
    assert await check(hass, calls)


async def test_condition_unknown_or_coarse_location_fails(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    place(hass, PHONE, None)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(hass, stop_device(hass, entry))
    assert not await check(hass, calls)
    await moved(hass, PHONE, 20, accuracy=200)
    assert not await check(hass, calls)


async def test_condition_any_target_is_enough(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 20)
    place(hass, TABLET, 300)
    entry = await setup_entry(hass)
    device_id = stop_device(hass, entry)
    calls = await at_stop_automation(hass, device_id, targets=[PHONE, TABLET])
    assert await check(hass, calls)


async def test_condition_all_needs_every_target(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 20)
    place(hass, TABLET, 300)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(
        hass, stop_device(hass, entry), targets=[PHONE, TABLET], options={"behavior": "all"}
    )
    assert not await check(hass, calls)
    await moved(hass, TABLET, 10)
    assert await check(hass, calls)


async def test_condition_removed_stop_fails(
    hass: HomeAssistant, hub: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    place(hass, PHONE, 20)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(hass, stop_device(hass, entry))
    stop_id = entry.get_subentries_of_type("stop")[0].subentry_id
    hass.config_entries.async_remove_subentry(entry, stop_id)
    await hass.async_block_till_done()
    assert not await check(hass, calls)
    assert not await check(hass, calls)
    assert caplog.text.count("is not a stop of GTFS Hub") == 1


async def test_condition_all_with_an_unknown_target_fails(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    hass.states.async_set(PHONE, "unknown")
    entry = await setup_entry(hass)
    calls = await at_stop_automation(hass, stop_device(hass, entry), options={"behavior": "all"})
    assert not await check(hass, calls)


async def test_leaving_waits_for_the_duration(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    place(hass, PHONE, 20)
    entry = await setup_entry(hass)
    calls = await automation(
        hass, "person_left_stop", stop_device(hass, entry), options={"for": {"seconds": 30}}
    )
    await moved(hass, PHONE, 150)
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await moved(hass, PHONE, 20)
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == []
    await moved(hass, PHONE, 150)
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_coarse_fix_far_away_fires_leaving(hass: HomeAssistant, hub: FakeHub) -> None:
    place(hass, PHONE, 20)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_left_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 250, accuracy=200)
    assert calls == []
    await moved(hass, PHONE, 2000, accuracy=200)
    assert len(calls) == 1
    assert calls[0].data["distance"] == 2000


async def test_moves_while_the_entry_reloads_are_learnt_again(
    hass: HomeAssistant, hub: FakeHub
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", stop_device(hass, entry))
    await moved(hass, PHONE, 20)
    assert len(calls) == 1
    await hass.config_entries.async_unload(entry.entry_id)
    await moved(hass, PHONE, 300)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await moved(hass, PHONE, 20)
    assert len(calls) == 2


async def test_condition_duration_counts_from_the_move(
    hass: HomeAssistant, hub: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    place(hass, PHONE, 300)
    entry = await setup_entry(hass)
    calls = await at_stop_automation(
        hass, stop_device(hass, entry), options={"for": {"seconds": 60}}
    )
    freezer.tick(timedelta(minutes=10))
    # The state stays not_home, so only the update time shows when the person came.
    await moved(hass, PHONE, 20)
    assert not await check(hass, calls)
    freezer.tick(timedelta(seconds=30))
    await moved(hass, PHONE, 25)
    assert not await check(hass, calls)
    freezer.tick(timedelta(seconds=31))
    assert await check(hass, calls)


async def test_condition_all_with_a_missing_target_fails(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await setup_entry(hass)
    calls = await at_stop_automation(
        hass, stop_device(hass, entry), targets=[TABLET], options={"behavior": "all"}
    )
    assert not await check(hass, calls)


@pytest.fixture
def stops(hub: FakeHub) -> FakeHub:
    """Serve stop A, its other pole and a stop 400 m on, on line 11."""
    hub.line_stops = [STOP_A, POLE, NEAR]
    return hub


@pytest.mark.parametrize("field", [None, "", []])
async def test_no_stop_covers_every_stop(
    hass: HomeAssistant, stops: FakeHub, field: str | list[str] | None
) -> None:
    place(hass, PHONE, -300)
    await setup_entry(hass, STOP_A, NEAR)
    calls = await automation(hass, "person_entered_stop", field)
    await moved(hass, PHONE, 0)
    await moved(hass, PHONE, 400)
    assert [call.data["stop"] for call in calls] == ["Alfa", "Near"]


async def test_no_stop_covers_a_stop_pinned_later(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, -300)
    entry = await setup_entry(hass)
    calls = await automation(hass, "person_entered_stop", None)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({"stop_key": NEAR.key}),
            subentry_type="stop",
            title=NEAR.name,
            unique_id=NEAR.key,
        ),
    )
    await hass.async_block_till_done()
    await moved(hass, PHONE, 400)
    assert [call.data["stop"] for call in calls] == ["Near"]


async def test_two_poles_fire_once(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, -300)
    await setup_entry(hass, STOP_A, POLE)
    calls = await automation(hass, "person_entered_stop", None)
    await moved(hass, PHONE, 0)
    await moved(hass, PHONE, 45)
    await moved(hass, PHONE, 20)
    assert [call.data["stop"] for call in calls] == ["Alfa"]


async def test_nearest_stop_wins(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, 300)
    await setup_entry(hass, STOP_A, POLE)
    calls = await automation(hass, "person_entered_stop", None)
    await moved(hass, PHONE, 40)
    assert [call.data["stop"] for call in calls] == ["Alfa 2"]
    assert calls[0].data["distance"] == 5


async def test_walking_to_another_stop_enters_it(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, 0)
    entry = await setup_entry(hass, STOP_A, NEAR)
    devices = [stop_device(hass, entry, "A"), stop_device(hass, entry, "N")]
    calls = await automation(hass, "person_entered_stop", devices)
    await moved(hass, PHONE, 10)
    await moved(hass, PHONE, 400)
    assert [call.data["stop"] for call in calls] == ["Near"]


async def test_walking_to_another_stop_leaves_the_first(
    hass: HomeAssistant, stops: FakeHub
) -> None:
    place(hass, PHONE, 0)
    entry = await setup_entry(hass, STOP_A, NEAR)
    devices = [stop_device(hass, entry, "A"), stop_device(hass, entry, "N")]
    calls = await automation(hass, "person_left_stop", devices)
    await moved(hass, PHONE, 10)
    await moved(hass, PHONE, 400)
    await moved(hass, PHONE, 1000)
    assert [(call.data["stop"], call.data["distance"]) for call in calls] == [
        ("Alfa", 400),
        ("Near", 600),
    ]


async def test_all_counts_targets_at_the_same_stop(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, -300)
    place(hass, TABLET, -300)
    await setup_entry(hass, STOP_A, NEAR)
    calls = await automation(
        hass, "person_entered_stop", None, targets=[PHONE, TABLET], options={"behavior": "all"}
    )
    await moved(hass, PHONE, 0)
    await moved(hass, TABLET, 400)
    assert calls == []
    await moved(hass, TABLET, 10)
    assert [call.data["stop"] for call in calls] == ["Alfa"]


async def test_duration_skips_a_pass_by_at_one_of_several_stops(
    hass: HomeAssistant, stops: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    place(hass, PHONE, -300)
    await setup_entry(hass, STOP_A, NEAR)
    calls = await automation(hass, "person_entered_stop", None, options={"for": {"seconds": 30}})
    await moved(hass, PHONE, 400)
    freezer.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await moved(hass, PHONE, 1000)
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == []


async def test_one_of_two_stops_removed_keeps_the_other(
    hass: HomeAssistant, stops: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    place(hass, PHONE, -300)
    entry = await setup_entry(hass, STOP_A, NEAR)
    devices = [stop_device(hass, entry, "A"), stop_device(hass, entry, "N")]
    calls = await automation(hass, "person_entered_stop", devices)
    hass.config_entries.async_remove_subentry(entry, subentry_id(entry, "N"))
    await hass.async_block_till_done()
    await moved(hass, PHONE, 400)
    await moved(hass, PHONE, 0)
    await moved(hass, PHONE, 400)
    assert [call.data["stop"] for call in calls] == ["Alfa"]
    assert caplog.text.count("is not a stop of GTFS Hub") == 1


async def test_target_at_a_removed_stop_is_learnt_again(
    hass: HomeAssistant, stops: FakeHub
) -> None:
    place(hass, PHONE, 400)
    entry = await setup_entry(hass, STOP_A, NEAR)
    calls = await automation(hass, "person_left_stop", None)
    await moved(hass, PHONE, 410)
    hass.config_entries.async_remove_subentry(entry, subentry_id(entry, "N"))
    await hass.async_block_till_done()
    await moved(hass, PHONE, 1000)
    # Near is gone, so walking away from it fires nothing, and Alfa still works.
    assert calls == []
    await moved(hass, PHONE, 0)
    await moved(hass, PHONE, 300)
    assert [call.data["stop"] for call in calls] == ["Alfa"]


async def test_no_stop_with_an_unloaded_entry_is_quiet(
    hass: HomeAssistant, stops: FakeHub, caplog: pytest.LogCaptureFixture
) -> None:
    place(hass, PHONE, -300)
    entry = await setup_entry(hass)
    await hass.config_entries.async_unload(entry.entry_id)
    calls = await automation(hass, "person_entered_stop", None)
    await moved(hass, PHONE, 0)
    assert calls == []
    assert "is not a stop of GTFS Hub" not in caplog.text


async def test_condition_no_stop_passes_at_any_stop(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, 400)
    await setup_entry(hass, STOP_A, NEAR)
    calls = await at_stop_automation(hass, None)
    assert await check(hass, calls)
    await moved(hass, PHONE, 200)
    assert not await check(hass, calls)


async def test_condition_two_stops_pass_at_either(hass: HomeAssistant, stops: FakeHub) -> None:
    place(hass, PHONE, 0)
    entry = await setup_entry(hass, STOP_A, NEAR)
    devices = [stop_device(hass, entry, "A"), stop_device(hass, entry, "N")]
    calls = await at_stop_automation(hass, devices)
    assert await check(hass, calls)
    await moved(hass, PHONE, 400)
    assert await check(hass, calls)
    hass.config_entries.async_remove_subentry(entry, subentry_id(entry, "A"))
    await hass.async_block_till_done()
    assert await check(hass, calls)
    await moved(hass, PHONE, 0)
    assert not await check(hass, calls)


async def test_readme_example_runs(
    hass: HomeAssistant, stops: FakeHub, freezer: FrozenDateTimeFactory
) -> None:
    """The README automation loads, notifies at any pinned stop and clears on leaving."""
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    section = readme[readme.index("## Stop presence") :]
    example = section[section.index("```yaml") + len("```yaml") : section.index("```\n", 10)]
    config = parse_yaml(example.replace("person.ania", "device_tracker.phone"))
    notes: list[ServiceCall] = async_mock_service(hass, "notify", "mobile_app_phone")
    stops.departures[NEAR.key] = [departure(), departure(minutes=9)]
    place(hass, PHONE, -300)
    await setup_entry(hass, STOP_A, NEAR)
    assert await async_setup_component(hass, "automation", {"automation": config})
    await hass.async_block_till_done()
    automation_state = hass.states.get("automation.automation_0")
    assert automation_state is not None
    assert automation_state.state == "on"
    await moved(hass, PHONE, 400)
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [note.data["title"] for note in notes] == ["Near"]
    assert notes[0].data["message"].count("\n") == 1
    await moved(hass, PHONE, 1000)
    assert notes[-1].data["message"] == "clear_notification"
