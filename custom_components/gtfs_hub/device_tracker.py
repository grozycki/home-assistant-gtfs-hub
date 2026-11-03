"""Vehicles of tracked lines on the map."""

from typing import TYPE_CHECKING, override

from homeassistant.components.device_tracker.entity import TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from gtfs_hub import LineKey

from .const import CONF_PROVIDER, CONF_ROUTE_TYPE, CONF_SHORT_NAME, DOMAIN, SUBENTRY_LINE
from .coordinator import DeparturesCoordinator, GtfsHubConfigEntry, TrackedVehicle
from .marker import form_of, marker_url, mode_of
from .recorder_load import async_check_recorder_load, tracker_entity_id
from .sightings import Sightings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a tracker for each vehicle as it appears, and remove those long gone."""
    coordinator = entry.runtime_data.departures
    if not coordinator.tracks_vehicles:
        return
    attribution = entry.runtime_data.provider.attribution
    provider = entry.data[CONF_PROVIDER]
    lines = {
        LineKey(item.data[CONF_ROUTE_TYPE], item.data[CONF_SHORT_NAME]): item
        for item in entry.get_subentries_of_type(SUBENTRY_LINE)
    }
    translations = await async_get_translations(hass, hass.config.language, "entity", {DOMAIN})

    def type_name(vehicle_type: str) -> str:
        return translations.get(_TYPE_TRANSLATION.format(vehicle_type), vehicle_type)

    registry = er.async_get(hass)
    sightings = Sightings(hass, entry.entry_id)
    await sightings.async_load(
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "device_tracker"
    )
    # Unique IDs of trackers added since setup. A key in the data already holds its line.
    known: set[str] = set()

    @callback
    def update() -> None:
        now = dt_util.utcnow()
        changed = False
        vehicles = coordinator.data.vehicles
        sightings.tick(now, live=bool(vehicles))
        for key, tracked in vehicles.items():
            line = lines.get(tracked.vehicle.line)
            if line is None:
                continue
            unique_id = tracker_unique_id(line, tracked)
            sightings.saw(unique_id)
            if unique_id in known:
                continue
            known.add(unique_id)
            changed = True
            async_add_entities(
                [VehicleTracker(coordinator, attribution, provider, type_name, key, line, tracked)],
                config_subentry_id=line.subentry_id,
            )
        for unique_id in sightings.sweep(now):
            known.discard(unique_id)
            if entity_id := registry.async_get_entity_id("device_tracker", DOMAIN, unique_id):
                registry.async_remove(entity_id)
                changed = True
        if changed:
            async_check_recorder_load(hass, entry)

    async_check_recorder_load(hass, entry)
    update()
    entry.async_on_unload(coordinator.async_add_listener(update))


_TYPE_TRANSLATION = (
    f"component.{DOMAIN}.entity.device_tracker.vehicle.state_attributes.vehicle_type.state.{{}}"
)


def vehicle_type(tracked: TrackedVehicle) -> str:
    """Return the type key of the vehicle planned for the current trip, such as bus_long_low."""
    vehicle = tracked.vehicle
    form = form_of(vehicle.planned_class).replace("-", "_")
    return f"{mode_of(vehicle.line.route_type)}_{form}"


def tracker_unique_id(line: ConfigSubentry, tracked: TrackedVehicle) -> str:
    """Return the identity of one vehicle on one tracked line."""
    return f"{line.subentry_id}_{tracked.vehicle.key}"


class VehicleTracker(CoordinatorEntity[DeparturesCoordinator], TrackerEntity):
    """One vehicle on one line. Its identity holds no trip, so a new trip keeps the entity."""

    _attr_has_entity_name = True
    # Translates the vehicle_type attribute. The name is set here, as it must follow the trip.
    _attr_translation_key = "vehicle"
    _unrecorded_attributes = frozenset({"trip_id", "derived_bearing", "headsign"})

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        provider: str,
        type_name: Callable[[str], str],
        key: str,
        line: ConfigSubentry,
        tracked: TrackedVehicle,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._type_name = type_name
        self._last = tracked
        self._was_available = True
        # With no zones given, a vehicle passing the house is not reported as home.
        self._attr_in_zones = []
        vehicle = tracked.vehicle
        self._attr_attribution = attribution
        self._attr_unique_id = tracker_unique_id(line, tracked)
        self._attr_name = self._name_of(tracked)
        number = vehicle.label or vehicle.key
        # A request: Home Assistant keeps the ID of a tracker it already knows.
        self.entity_id = tracker_entity_id(provider, vehicle.line.short_name, number)

    def _name_of(self, tracked: TrackedVehicle) -> str:
        """Return line, type and number. Trackers have no device, so the name carries the line."""
        vehicle = tracked.vehicle
        number = f"#{vehicle.label}" if vehicle.label else vehicle.key
        planned = vehicle.planned_class
        # The source's own words come first; the translated type covers classes without them.
        kind = (
            planned.description
            if planned is not None and planned.description
            else self._type_name(vehicle_type(tracked))
        )
        return f"{vehicle.line.short_name} - {kind} {number}"

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
        vehicle = self._last.vehicle
        return marker_url(
            vehicle.line.route_type,
            form_of(vehicle.planned_class),
            vehicle.line.short_name,
            self._last.derived_bearing,
            self.coordinator.hub.provider.livery,
        )

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, object]:
        """Return what the feed says, and the direction worked out from movement."""
        vehicle = self._last.vehicle
        planned = vehicle.planned_class
        return {
            "planned_vehicle": planned.name if planned else None,
            "length": planned.length if planned else None,
            "low_floor": planned.low_floor if planned else None,
            "line": vehicle.line.short_name,
            "vehicle_type": vehicle_type(self._last),
            "fleet_number": vehicle.label,
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
            # The next trip may plan another type, and the name follows it.
            name = self._name_of(tracked)
            if name != self._attr_name:
                self._attr_name = name
        self.async_write_ha_state()
