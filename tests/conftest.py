"""Fixtures: Home Assistant with a fake hub, as the library has its own tests."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from custom_components.gtfs_hub.const import DOMAIN
from homeassistant.config_entries import ConfigSubentryData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from gtfs_hub import (
    Capabilities,
    Departure,
    DepartureStatus,
    GtfsNoDataError,
    Line,
    LineKey,
    RealtimeData,
    StaticFeedRef,
    Stop,
    StopKey,
    TripId,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Generator

    from aiohttp import ClientSession
    from homeassistant.core import HomeAssistant, State

LINE_11 = LineKey(3, "11")
TRAM_11 = LineKey(0, "11")
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
    synced: list[list[LineKey]] = field(default_factory=list)

    async def async_sync(self, selection: Collection[LineKey]) -> None:
        self.synced.append(list(selection))
        if self.sync_error:
            raise self.sync_error

    async def async_stops_of_line(self, key: LineKey) -> list[Stop]:
        if not self.ready:
            raise GtfsNoDataError("empty")
        return [STOP_A, STOP_B]

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
        if self.realtime_error:
            raise self.realtime_error
        return self.realtime

    async def async_departures(self, key: StopKey, **kwargs: object) -> list[Departure]:
        return self.departures.get(key, [])


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations: None) -> None:
    """Load custom integrations in every test."""


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
    *subentries: ConfigSubentryData, options: dict[str, list[str]] | None = None
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Fake",
        data={"provider": "fake"},
        options=options or {},
        unique_id="fake",
        subentries_data=list(subentries),
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
