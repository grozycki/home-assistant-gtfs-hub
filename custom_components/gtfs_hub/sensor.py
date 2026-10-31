"""Departure sensors."""

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from gtfs_hub import Departure, DepartureStatus, Stop

from . import GtfsHubConfigEntry
from .const import CONF_PERSONS, CONF_STOP_KEY, SUBENTRY_STOP
from .coordinator import DeparturesCoordinator
from .entity import GtfsHubEntity, departure_as_dict, next_departure, service_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for pinned stops and chosen persons."""
    coordinator = entry.runtime_data.departures
    attribution = entry.runtime_data.provider.attribution
    for subentry in entry.get_subentries_of_type(SUBENTRY_STOP):
        async_add_entities(
            [
                NextDepartureSensor(coordinator, attribution, subentry),
                DelaySensor(coordinator, attribution, subentry),
            ],
            config_subentry_id=subentry.subentry_id,
        )
    async_add_entities(
        NearestStopSensor(coordinator, attribution, entry, person)
        for person in entry.options.get(CONF_PERSONS, [])
    )


class StopSensor(GtfsHubEntity, SensorEntity):
    """A sensor of one pinned stop."""

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        subentry: ConfigSubentry,
    ) -> None:
        super().__init__(coordinator, attribution)
        self._stop_key: str = subentry.data[CONF_STOP_KEY]
        self._attr_device_info = service_device(subentry.subentry_id, subentry.title)
        self._attr_unique_id = f"{subentry.subentry_id}_{self._attr_translation_key}"

    @property
    def _departures(self) -> list[Departure]:
        return self.coordinator.data.departures.get(self._stop_key, [])

    @property
    def _next(self) -> Departure | None:
        return next_departure(self._departures)


class NextDepartureSensor(StopSensor):
    """The next departure a rider can catch."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_departure"
    _unrecorded_attributes = frozenset({"departures"})

    @property
    def native_value(self) -> datetime | None:
        """Return the predicted time, or the scheduled one."""
        departure = self._next
        return departure.expected if departure else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the upcoming list and the link to everything else."""
        departure = self._next
        schedule = self.coordinator.schedule.data
        stop = schedule.stops.get(self._stop_key) if schedule else None
        return {
            "line": departure.line.short_name if departure else None,
            "headsign": departure.headsign if departure else None,
            "realtime": departure is not None and departure.status is DepartureStatus.PREDICTED,
            "timetable_url": stop.url if stop else None,
            "departures": [departure_as_dict(item) for item in self._departures],
        }


class DelaySensor(StopSensor):
    """The delay of the next departure. Unknown is not on time."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_translation_key = "delay"

    @property
    def native_value(self) -> int | None:
        """Return the delay in seconds."""
        departure = self._next
        return departure.delay if departure else None


class NearestStopSensor(GtfsHubEntity, SensorEntity):
    """The stop a person is near, so one sensor serves every stop of the lines."""

    _attr_translation_key = "nearest_stop"
    _unrecorded_attributes = frozenset({"departures"})

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        entry: GtfsHubConfigEntry,
        person: str,
    ) -> None:
        super().__init__(coordinator, attribution)
        self._person = person
        self._attr_device_info = service_device(entry.entry_id, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_nearest_{person}"
        self._attr_translation_placeholders = {"person": person.removeprefix("person.")}

    @property
    def _stop(self) -> Stop | None:
        item = self.coordinator.data.nearest.get(self._person)
        return item.stop if item else None

    @property
    def native_value(self) -> str | None:
        """Return the name of the stop."""
        stop = self._stop
        return stop.name if stop else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return where the stop is and what leaves it."""
        item = self.coordinator.data.nearest.get(self._person)
        if item is None:
            return {"departures": []}
        departures = self.coordinator.data.departures.get(item.stop.key, [])
        return {
            "stop_code": item.stop.code,
            "distance": round(item.distance),
            "distance_unit": UnitOfLength.METERS,
            "timetable_url": item.stop.url,
            "departures": [departure_as_dict(departure) for departure in departures],
        }
