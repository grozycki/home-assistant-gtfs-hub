"""Fixtures: Home Assistant with a fake hub, as the library has its own tests."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DOMAIN
from gtfs_hub.realtime import TripUpdate
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.syrupy import HomeAssistantSnapshotExtension

from gtfs_hub import (
    Capabilities,
    Departure,
    DepartureStatus,
    GtfsNoDataError,
    Line,
    LineKey,
    Livery,
    RealtimeData,
    Shape,
    Snapshot,
    StaticFeedRef,
    Stop,
    StopChoice,
    StopKey,
    TripDelay,
    TripId,
    Vehicle,
    VehicleKey,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Generator

    from aiohttp import ClientSession
    from homeassistant.core import HomeAssistant, State
    from syrupy.assertion import SnapshotAssertion

LINE_11 = LineKey(3, "11")
TRAM_11 = LineKey(0, "11")
LINE_11_URL = "https://example.test/lines/11"
STOP_A = Stop(StopKey("A"), "Alfa", "C-A", 50.0, 19.0, "https://example.test/a")
STOP_B = Stop(StopKey("B"), "Beta", None, 50.1, 19.1)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def departure(
    minutes: int = 5,
    delay: int | None = None,
    status: DepartureStatus = DepartureStatus.SCHEDULED,
    stop_key: str = "A",
) -> Departure:
    scheduled = NOW + timedelta(minutes=minutes)
    predicted = scheduled + timedelta(seconds=delay) if delay is not None else None
    return Departure(
        StopKey(stop_key),
        LINE_11,
        "Terminus",
        scheduled,
        predicted,
        delay,
        status,
        TripId("t1"),
        date(2026, 11, 2),
    )


class FakeProvider:
    key = "fake"
    name = "Fake"
    attribution = "Data: Fake"
    stop_key = "stop_id"
    capabilities = Capabilities()
    livery: Livery | None = None

    async def async_list_static_feeds(self, session: ClientSession) -> list[StaticFeedRef]:
        return []


@dataclass
class FakeHub:
    """Stands in for GtfsHub."""

    ready: bool = True
    sync_error: Exception | None = None
    realtime_error: Exception | None = None
    realtime: RealtimeData | None = None
    departures: dict[StopKey, list[Departure]] = field(default_factory=dict)
    line_stops: list[Stop] = field(default_factory=lambda: [STOP_A, STOP_B])
    synced: list[list[LineKey]] = field(default_factory=list)
    vehicles: list[Vehicle] = field(default_factory=list)
    # When set, each fetch returns a fresh message, and these are its trips.
    trip_delays: list[TripDelay] | None = None
    realtime_fetches: int = 0
    shapes: dict[LineKey, list[Shape]] = field(default_factory=dict)
    shape_dates: list[date | None] = field(default_factory=list)
    provider: FakeProvider = field(default_factory=lambda: FakeProvider())

    async def async_sync(self, selection: Collection[LineKey]) -> None:
        self.synced.append(list(selection))
        if self.sync_error:
            raise self.sync_error

    async def async_stops_of_line(self, key: LineKey) -> list[Stop]:
        if not self.ready:
            raise GtfsNoDataError("empty")
        return self.line_stops

    async def async_line_shapes(
        self, key: LineKey, service_date: date | None = None
    ) -> list[Shape]:
        self.shape_dates.append(service_date)
        if not self.ready:
            raise GtfsNoDataError("empty")
        return self.shapes.get(key, [])

    async def async_fetch_vehicles(self) -> list[Vehicle]:
        return self.vehicles

    async def async_snapshots(self) -> list[Snapshot]:
        if not self.ready:
            raise GtfsNoDataError("empty")
        return []

    async def async_list_lines(self) -> list[Line]:
        if not self.ready:
            raise GtfsNoDataError("empty")
        return [
            Line(LINE_11, "Long", "A", None, None, LINE_11_URL),
            Line(TRAM_11, None, "A", None, None),
            Line(LineKey(1400, "F"), "Funicular", "A", None, None),
        ]

    async def async_stop_choices(self, lines: Collection[LineKey]) -> list[StopChoice]:
        return [
            StopChoice(STOP_A, ("11",), ("North",)),
            StopChoice(STOP_B, ("11",), ()),
        ]

    async def async_search_lines(self, query: str, limit: int = 20) -> list[Line]:
        if not self.ready:
            raise GtfsNoDataError("empty")
        if query == "none":
            return []
        return [Line(LINE_11, "Long", "A", None, None), Line(TRAM_11, None, "A", None, None)]

    async def async_search_stops(
        self, query: str, limit: int = 20, only: Collection[StopKey] | None = None
    ) -> list[Stop]:
        stops = [STOP_A, Stop(StopKey("Z"), "Other line", None, 1, 2)] if query != "none" else []
        return [stop for stop in stops if only is None or stop.key in only][:limit]

    async def async_fetch_realtime(self) -> RealtimeData | None:
        self.realtime_fetches += 1
        if self.realtime_error:
            raise self.realtime_error
        if self.trip_delays is not None:
            trips: dict[str, TripUpdate] = {
                item.trip_id: TripUpdate(item.trip_id, None, False, ()) for item in self.trip_delays
            }
            idle = {"idle": TripUpdate("idle", None, False, ())}
            return RealtimeData(dt_util.utcnow(), trips or idle)
        return self.realtime

    async def async_trip_delays(self, realtime: RealtimeData) -> list[TripDelay]:
        return list(self.trip_delays or [])

    async def async_departures(self, key: StopKey, **kwargs: object) -> list[Departure]:
        return self.departures.get(key, [])


@pytest.fixture
def hub() -> Generator[FakeHub]:
    fake = FakeHub()
    with (
        patch("custom_components.gtfs_hub.GtfsHub", return_value=fake),
        patch(
            "custom_components.gtfs_hub.async_create_provider",
            return_value=FakeProvider(),
        ),
        patch(
            "custom_components.gtfs_hub.config_flow.async_list_provider_keys",
            return_value=["fake"],
        ),
    ):
        yield fake


def make_entry(
    *subentries: ConfigSubentryData, options: dict[str, list[str] | int] | None = None
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Fake",
        data={"provider": "fake"},
        options=options or {},
        unique_id="fake",
        subentries_data=list(subentries),
        minor_version=2,
    )


def line_subentry() -> ConfigSubentryData:
    return ConfigSubentryData(
        data={"route_type": 3, "short_name": "11"},
        subentry_type="line",
        title="Bus 11",
        unique_id="3:11",
    )


def stop_subentry() -> ConfigSubentryData:
    return ConfigSubentryData(
        data={"stop_key": "A"}, subentry_type="stop", title="Alfa", unique_id="A"
    )


def get_state(hass: HomeAssistant, entity_id: str) -> State:
    """Return a state that must exist."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def vehicle(
    trip: str = "t1",
    latitude: float = 50.0,
    longitude: float = 19.0,
    label: str | None = None,
    shape: Shape | None = None,
) -> Vehicle:
    return Vehicle(
        VehicleKey("v1"),
        LINE_11,
        TripId(trip),
        "Terminus",
        latitude,
        longitude,
        NOW,
        label=label,
        shape=shape,
    )


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Read snapshots from tests/snapshots in any plugin order.

    Both syrupy and the Home Assistant plugin define this fixture, and which wins
    depends on the order the plugins load in, so a run could look in __snapshots__.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
