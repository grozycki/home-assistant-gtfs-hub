"""Vehicles of tracked lines on the map."""

from typing import TYPE_CHECKING, override

from homeassistant.components.device_tracker.entity import TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from gtfs_hub import LineKey

from .const import CONF_ROUTE_TYPE, CONF_SHORT_NAME, SUBENTRY_LINE
from .coordinator import DeparturesCoordinator, GtfsHubConfigEntry, TrackedVehicle
from .marker import marker_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a tracker for each vehicle as it appears."""
    coordinator = entry.runtime_data.departures
    if not coordinator.tracks_vehicles:
        return
    attribution = entry.runtime_data.provider.attribution
    lines = {
        LineKey(item.data[CONF_ROUTE_TYPE], item.data[CONF_SHORT_NAME]): item.subentry_id
        for item in entry.get_subentries_of_type(SUBENTRY_LINE)
    }
    known: set[str] = set()

    @callback
    def add_new() -> None:
        for key, tracked in coordinator.data.vehicles.items():
            subentry_id = lines.get(tracked.vehicle.line)
            if key in known or subentry_id is None:
                continue
            known.add(key)
            async_add_entities(
                [VehicleTracker(coordinator, attribution, key, subentry_id, tracked)],
                config_subentry_id=subentry_id,
            )

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class VehicleTracker(CoordinatorEntity[DeparturesCoordinator], TrackerEntity):
    """One vehicle. Its identity holds no trip, so a new trip keeps the entity."""

    _unrecorded_attributes = frozenset({"trip_id", "derived_bearing", "headsign"})

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        key: str,
        subentry_id: str,
        tracked: TrackedVehicle,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._last = tracked
        self._was_available = True
        # With no zones given, a vehicle passing the house is not reported as home.
        self._attr_in_zones = []
        line = tracked.vehicle.line
        self._attr_attribution = attribution
        self._attr_unique_id = f"{subentry_id}_{tracked.vehicle.key}"
        self._attr_name = f"{line.short_name} {tracked.vehicle.key}"

    @property
    def _tracked(self) -> TrackedVehicle | None:
        return self.coordinator.data.vehicles.get(self._key)

    @property
    @override
    def available(self) -> bool:
        """Return whether the vehicle is in the feed, which keeps it off the map otherwise."""
        return super().available and self._tracked is not None

    @property
    @override
    def latitude(self) -> float:
        """Return the latitude."""
        return self._last.vehicle.latitude

    @property
    @override
    def longitude(self) -> float:
        """Return the longitude."""
        return self._last.vehicle.longitude

    @property
    @override
    def entity_picture(self) -> str:
        """Return a marker with the line and direction."""
        line = self._last.vehicle.line
        return marker_url(line.route_type, line.short_name, self._last.derived_bearing)

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, object]:
        """Return what the feed says, and the direction worked out from movement."""
        vehicle = self._last.vehicle
        return {
            "line": vehicle.line.short_name,
            "headsign": vehicle.headsign,
            "trip_id": vehicle.trip_id,
            "derived_bearing": self._last.derived_bearing,
        }

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Write only changes, as a tracker stores every write."""
        tracked = self._tracked
        available = self.available
        if tracked == self._last and available == self._was_available:
            return
        self._was_available = available
        if tracked is not None:
            self._last = tracked
        self.async_write_ha_state()
