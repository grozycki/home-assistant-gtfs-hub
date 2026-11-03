"""Fetch the schedule and the departures, on their two different clocks."""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from gtfs_hub import (
    Departure,
    GtfsError,
    GtfsHub,
    GtfsNoDataError,
    Line,
    LineKey,
    Provider,
    RealtimeData,
    Stop,
    StopKey,
    TripId,
    Vehicle,
    locate,
)

from .const import (
    CONF_DELAY_THRESHOLD,
    CONF_LIVE_INTERVAL,
    CONF_PERSONS,
    CONF_ROUTE_TYPE,
    CONF_SHORT_NAME,
    CONF_STOP_KEY,
    DEFAULT_DELAY_THRESHOLD,
    DEMO_VEHICLES_VARIABLE,
    DEPARTURES_INTERVAL,
    DOMAIN,
    LIST_LIMIT,
    SCHEDULE_INTERVAL,
    SCHEDULE_RETRY_INTERVAL,
    SUBENTRY_LINE,
    SUBENTRY_STOP,
)
from .delays import DelayEvent, DelayRules
from .nearest import bearing, distance, find_nearest

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_MAX_AGE = timedelta(minutes=5)
MIN_MOVEMENT = 20
EMPTY_FEED_GRACE = timedelta(minutes=2)


def vehicle_id(vehicle: Vehicle) -> str:
    """Return an identity free of trips, as a new trip must not make a new entity."""
    return f"{vehicle.line.route_type}:{vehicle.line.short_name}:{vehicle.key}"


@dataclass(frozen=True, slots=True)
class ScheduleData:
    """The selected lines and their stops."""

    stops: dict[StopKey, Stop]
    lines: dict[LineKey, Line] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NearestStop:
    """Where a person is, relative to the stops."""

    stop: Stop
    distance: float


@dataclass(frozen=True, slots=True)
class _Heading:
    """Where a vehicle was when its direction was last worked out."""

    latitude: float
    longitude: float
    bearing: float | None
    trip_id: TripId
    # Metres along the shape of that trip, when it was located on it.
    along: float | None


@dataclass(frozen=True, slots=True)
class TrackedVehicle:
    """A vehicle with a direction worked out from its shape or its movement."""

    vehicle: Vehicle
    derived_bearing: float | None


@dataclass(frozen=True, slots=True)
class DeparturesData:
    """One tick of departures."""

    departures: dict[StopKey, list[Departure]] = field(default_factory=dict)
    nearest: dict[str, NearestStop | None] = field(default_factory=dict)
    live: bool = False
    vehicles: dict[str, TrackedVehicle] = field(default_factory=dict)
    delay_events: tuple[DelayEvent, ...] = ()
    # Counts ticks, so a listener called twice with one tick fires its events once.
    tick: int = 0


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
            lines = {line.key: line for line in await self.hub.async_list_lines()}
        except GtfsNoDataError as err:
            raise UpdateFailed(f"No schedule available: {err}") from err
        return ScheduleData(stops, {key: lines[key] for key in selection if key in lines})


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
        self._headings: dict[str, _Heading] = {}
        self._vehicles_seen: datetime | None = None
        threshold = entry.options.get(CONF_DELAY_THRESHOLD, DEFAULT_DELAY_THRESHOLD)
        self._rules = DelayRules(int(threshold) * 60)
        self._tick = 0

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
            vehicles = []
        now = dt_util.utcnow()
        # A whole feed goes empty for a moment, which says nothing about the vehicles.
        if not vehicles:
            if self._vehicles_seen is not None and now - self._vehicles_seen < EMPTY_FEED_GRACE:
                return self.data.vehicles
            return {}
        self._vehicles_seen = now
        tracked = {
            vehicle_id(vehicle): TrackedVehicle(vehicle, self._direction(vehicle))
            for vehicle in vehicles
        }
        # A vehicle back after a gap is located afresh, so nothing else needs keeping.
        self._headings = {key: self._headings[key] for key in tracked}
        return tracked

    def _direction(self, vehicle: Vehicle) -> float | None:
        """Work out a direction from the shape, or once a vehicle has moved far enough."""
        key = vehicle_id(vehicle)
        held = self._headings.get(key)
        # A new trip starts its shape afresh.
        after = held.along if held is not None and held.trip_id == vehicle.trip_id else None
        located = (
            None
            if vehicle.shape is None
            else locate(vehicle.shape, vehicle.latitude, vehicle.longitude, after)
        )
        if located is not None:
            self._headings[key] = _Heading(
                vehicle.latitude, vehicle.longitude, located.bearing, vehicle.trip_id, located.along
            )
            return located.bearing
        if held is None:
            self._headings[key] = _Heading(
                vehicle.latitude, vehicle.longitude, None, vehicle.trip_id, None
            )
            return None
        if (
            distance(held.latitude, held.longitude, vehicle.latitude, vehicle.longitude)
            < MIN_MOVEMENT
        ):
            return held.bearing
        direction = bearing(held.latitude, held.longitude, vehicle.latitude, vehicle.longitude)
        self._headings[key] = _Heading(
            vehicle.latitude, vehicle.longitude, direction, vehicle.trip_id, after
        )
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
        self._tick += 1
        if not wanted and not self.schedule.selection():
            self.realtime = None
            return DeparturesData(nearest=nearest, vehicles=vehicles, tick=self._tick)
        await self._async_fetch_realtime()
        realtime = self.realtime
        now = dt_util.utcnow()
        fresh = (
            realtime
            if realtime is not None and realtime.trips and not realtime.is_stale(now, _MAX_AGE)
            else None
        )
        departures = {}
        try:
            for key in wanted:
                departures[key] = await self.hub.async_departures(
                    key, realtime=realtime, limit=LIST_LIMIT, max_age=_MAX_AGE
                )
            trips = await self.hub.async_trip_delays(fresh) if fresh is not None else None
        except GtfsNoDataError as err:
            raise UpdateFailed(f"No schedule available: {err}") from err
        # Missing or stale live data pauses the rules rather than reading as on time.
        events = tuple(self._rules.update(trips, now))
        live = fresh is not None
        return DeparturesData(departures, nearest, live, vehicles, events, self._tick)


@dataclass(slots=True)
class GtfsHubRuntime:
    """What one entry keeps while it is loaded."""

    provider: Provider
    hub: GtfsHub
    schedule: ScheduleCoordinator
    departures: DeparturesCoordinator
    applying: bool = False


type GtfsHubConfigEntry = ConfigEntry[GtfsHubRuntime]
