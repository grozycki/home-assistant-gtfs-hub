"""Shared base entity."""

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from gtfs_hub import Departure, DepartureStatus

from .const import DOMAIN
from .coordinator import DeparturesCoordinator

if TYPE_CHECKING:
    from homeassistant.util.json import JsonValueType

    from .badge import LineBadge


def departure_as_dict(departure: Departure, badge: LineBadge) -> dict[str, JsonValueType]:
    """Return a departure in machine-readable, JSON-safe form, with how to show its line."""
    return {
        "line": departure.line.short_name,
        "route_type": departure.line.route_type,
        "line_color": badge.background,
        "line_text_color": badge.text,
        "headsign": departure.headsign,
        "scheduled": departure.scheduled.isoformat(),
        "predicted": departure.predicted.isoformat() if departure.predicted else None,
        "delay": departure.delay,
        "status": departure.status.value,
    }


def next_departure(departures: list[Departure]) -> Departure | None:
    """Return the first departure a rider can still catch."""
    gone = (DepartureStatus.CANCELLED, DepartureStatus.SKIPPED)
    return next((item for item in departures if item.status not in gone), None)


class GtfsHubEntity(CoordinatorEntity[DeparturesCoordinator]):
    """An entity fed by the departures coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DeparturesCoordinator, attribution: str | None) -> None:
        super().__init__(coordinator)
        self._attr_attribution = attribution


def service_device(identifier: str, name: str) -> DeviceInfo:
    """Return a service device, as neither a stop nor a provider is hardware."""
    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        name=name,
        entry_type=DeviceEntryType.SERVICE,
    )
