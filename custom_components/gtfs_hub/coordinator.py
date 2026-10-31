"""Fetch the schedule and the departures, on their two different clocks."""

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from gtfs_hub import (
    Departure,
    GtfsError,
    GtfsHub,
    GtfsNoDataError,
    LineKey,
    Provider,
    RealtimeData,
    Stop,
    StopKey,
    Vehicle,
)

from .const import (
    CONF_LIVE_INTERVAL,
    CONF_PERSONS,
    CONF_ROUTE_TYPE,
    CONF_SHORT_NAME,
    CONF_STOP_KEY,
    DEMO_VEHICLES_VARIABLE,
    DEPARTURES_INTERVAL,
    DOMAIN,
    LIST_LIMIT,
    SCHEDULE_INTERVAL,
    SCHEDULE_RETRY_INTERVAL,
    SUBENTRY_LINE,
    SUBENTRY_STOP,
)
from .nearest import bearing, distance, find_nearest

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_MAX_AGE = timedelta(minutes=5)
MIN_MOVEMENT = 20


def vehicle_id(vehicle: Vehicle) -> str:
    """Return an identity free of trips, as a new trip must not make a new entity."""
    return f"{vehicle.line.route_type}:{vehicle.line.short_name}:{vehicle.key}"


@dataclass(frozen=True, slots=True)
class ScheduleData:
    """The stops of the selected lines."""

    stops: dict[StopKey, Stop]


@dataclass(frozen=True, slots=True)
class NearestStop:
    """Where a person is, relative to the stops."""

    stop: Stop
    distance: float


@dataclass(frozen=True, slots=True)
class TrackedVehicle:
    """A vehicle with a direction worked out from its movement."""

    vehicle: Vehicle
    derived_bearing: float | None


@dataclass(frozen=True, slots=True)
class DeparturesData:
    """One tick of departures."""

    departures: dict[StopKey, list[Departure]] = field(default_factory=dict)
    nearest: dict[str, NearestStop | None] = field(default_factory=dict)
    live: bool = False
    vehicles: dict[str, TrackedVehicle] = field(default_factory=dict)


class ScheduleCoordinator(DataUpdateCoordinator[ScheduleData]):
    """Keep the local schedule in line with the source and the selection."""

    config_entry: GtfsHubConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GtfsHubConfigEntry, hub: GtfsHub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_schedule",
            update_interval=SCHEDULE_INTERVAL,
        )
        self.hub = hub
        # Stays False until a first sync gives something to read.
        self.data = ScheduleData({})
        self.last_update_success = False

    def selection(self) -> list[LineKey]:
        """Return the lines chosen in subentries."""
        return [
            LineKey(subentry.data[CONF_ROUTE_TYPE], subentry.data[CONF_SHORT_NAME])
            for subentry in self.config_entry.get_subentries_of_type(SUBENTRY_LINE)
        ]

    @override
    async def _async_update_data(self) -> ScheduleData:
        selection = self.selection()
        try:
            await self.hub.async_sync(selection)
            self.update_interval = SCHEDULE_INTERVAL
        except GtfsError as err:
            self.update_interval = SCHEDULE_RETRY_INTERVAL
            # The stored database may still answer, so only warn when it does.
            _LOGGER.warning("Schedule sync failed, keeping stored data: %s", err)
        try:
            await self.hub.async_snapshots()
            stops: dict[StopKey, Stop] = {}
            for line in selection:
                for stop in await self.hub.async_stops_of_line(line):
                    stops[stop.key] = stop
        except GtfsNoDataError as err:
            raise UpdateFailed(f"No schedule available: {err}") from err
        return ScheduleData(stops)


class DeparturesCoordinator(DataUpdateCoordinator[DeparturesData]):
    """Work out departures on the realtime clock."""

    config_entry: GtfsHubConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GtfsHubConfigEntry,
        hub: GtfsHub,
        schedule: ScheduleCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_departures",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_LIVE_INTERVAL, DEPARTURES_INTERVAL.total_seconds())
            ),
        )
        self.hub = hub
        self.schedule = schedule
        self.realtime: RealtimeData | None = None
        self._feed_down = False
        # Entities exist before the first sync ends, and stay unavailable until then.
        self.data = DeparturesData()
        self.last_update_success = False
        self.tracks_vehicles = (
            hub.provider.capabilities.stable_vehicle_id
            or os.environ.get(DEMO_VEHICLES_VARIABLE) == "1"
        )
        self._anchors: dict[str, tuple[float, float, float | None]] = {}

    def _pinned(self) -> list[StopKey]:
        return [
            StopKey(subentry.data[CONF_STOP_KEY])
            for subentry in self.config_entry.get_subentries_of_type(SUBENTRY_STOP)
        ]

    def _nearest(self, stops: dict[StopKey, Stop]) -> dict[str, NearestStop | None]:
        found: dict[str, NearestStop | None] = {}
        for person in self.config_entry.options.get(CONF_PERSONS, []):
            state = self.hass.states.get(person)
            latitude = state.attributes.get("latitude") if state else None
            longitude = state.attributes.get("longitude") if state else None
            found[person] = None
            if latitude is None or longitude is None:
                continue
            previous = self.data.nearest.get(person)
            result = find_nearest(
                latitude, longitude, stops.values(), previous.stop.key if previous else None
            )
            if result is not None:
                found[person] = NearestStop(*result)
        return found

    async def _async_fetch_realtime(self) -> None:
        try:
            self.realtime = await self.hub.async_fetch_realtime()
        except GtfsError as err:
            self.realtime = None
            if not self._feed_down:
                _LOGGER.warning("Live data unavailable, showing the schedule: %s", err)
            self._feed_down = True
        else:
            if self._feed_down:
                _LOGGER.info("Live data is back")
            self._feed_down = False

    async def _async_vehicles(self) -> dict[str, TrackedVehicle]:
        if not self.tracks_vehicles:
            return {}
        try:
            vehicles = await self.hub.async_fetch_vehicles()
        except GtfsError as err:
            _LOGGER.debug("Vehicle positions unavailable: %s", err)
            return {}
        return {
            vehicle_id(vehicle): TrackedVehicle(vehicle, self._direction(vehicle))
            for vehicle in vehicles
        }

    def _direction(self, vehicle: Vehicle) -> float | None:
        """Work out a direction once a vehicle has moved far enough to trust it."""
        key = vehicle_id(vehicle)
        anchor = self._anchors.get(key)
        if anchor is None:
            self._anchors[key] = (vehicle.latitude, vehicle.longitude, None)
            return None
        latitude, longitude, held = anchor
        if distance(latitude, longitude, vehicle.latitude, vehicle.longitude) < MIN_MOVEMENT:
            return held
        direction = bearing(latitude, longitude, vehicle.latitude, vehicle.longitude)
        self._anchors[key] = (vehicle.latitude, vehicle.longitude, direction)
        return direction

    @override
    async def _async_update_data(self) -> DeparturesData:
        if not self.schedule.last_update_success:
            raise UpdateFailed("Waiting for the first schedule sync")
        stops = self.schedule.data.stops
        nearest = self._nearest(stops)
        wanted = set(self._pinned())
        wanted.update(item.stop.key for item in nearest.values() if item is not None)
        vehicles = await self._async_vehicles()
        if not wanted:
            self.realtime = None
            return DeparturesData(nearest=nearest, vehicles=vehicles)
        await self._async_fetch_realtime()
        realtime = self.realtime
        live = realtime is not None and not realtime.is_stale(dt_util.utcnow(), _MAX_AGE)
        departures = {}
        try:
            for key in wanted:
                departures[key] = await self.hub.async_departures(
                    key, realtime=realtime, limit=LIST_LIMIT, max_age=_MAX_AGE
                )
        except GtfsNoDataError as err:
            raise UpdateFailed(f"No schedule available: {err}") from err
        return DeparturesData(departures, nearest, live, vehicles)


@dataclass(slots=True)
class GtfsHubRuntime:
    """What one entry keeps while it is loaded."""

    provider: Provider
    hub: GtfsHub
    schedule: ScheduleCoordinator
    departures: DeparturesCoordinator
    applying: bool = False


type GtfsHubConfigEntry = ConfigEntry[GtfsHubRuntime]
