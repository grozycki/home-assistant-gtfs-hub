"""Delay events of tracked lines, for automations to trigger on."""

from typing import TYPE_CHECKING, override

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback

from gtfs_hub import LineKey

from .const import CONF_ROUTE_TYPE, CONF_SHORT_NAME, SUBENTRY_LINE
from .delays import DelayEvent, DelayEventType
from .entity import GtfsHubEntity, service_device

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeparturesCoordinator, GtfsHubConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a delay event entity to each tracked line."""
    coordinator = entry.runtime_data.departures
    attribution = entry.runtime_data.provider.attribution
    for subentry in entry.get_subentries_of_type(SUBENTRY_LINE):
        async_add_entities(
            [LineDelayEvent(coordinator, attribution, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


def event_data(event: DelayEvent) -> dict[str, str | int | None]:
    """Return what an automation needs to know about the trip."""
    trip = event.trip
    return {
        "line": trip.line.short_name,
        "route_type": trip.line.route_type,
        "headsign": trip.headsign,
        "trip_id": trip.trip_id,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "delay": trip.delay,
    }


class LineDelayEvent(GtfsHubEntity, EventEntity):
    """Delays and cancellations on one line, fired once per trip."""

    _attr_translation_key = "delays"

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        subentry: ConfigSubentry,
    ) -> None:
        super().__init__(coordinator, attribution)
        self._line = LineKey(subentry.data[CONF_ROUTE_TYPE], subentry.data[CONF_SHORT_NAME])
        self._tick = coordinator.data.tick
        self._attr_event_types = [item.value for item in DelayEventType]
        self._attr_unique_id = f"{subentry.subentry_id}_delays"
        self._attr_device_info = service_device(subentry.subentry_id, subentry.title)

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Fire this line's events of a new tick."""
        data = self.coordinator.data
        if data.tick == self._tick:
            super()._handle_coordinator_update()
            return
        self._tick = data.tick
        fired = False
        for event in data.delay_events:
            if event.trip.line == self._line:
                self._trigger_event(event.type, event_data(event))
                self.async_write_ha_state()
                fired = True
        if not fired:
            super()._handle_coordinator_update()
