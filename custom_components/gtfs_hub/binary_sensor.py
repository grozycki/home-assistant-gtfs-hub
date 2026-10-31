"""Tell whether live data is available."""

from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .entity import GtfsHubEntity, service_device

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import DeparturesCoordinator, GtfsHubConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GtfsHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the live data sensor."""
    runtime = entry.runtime_data
    async_add_entities([LiveDataSensor(runtime.departures, runtime.provider.attribution, entry)])


class LiveDataSensor(GtfsHubEntity, BinarySensorEntity):
    """On while fresh realtime data is in use."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "live_data"

    def __init__(
        self,
        coordinator: DeparturesCoordinator,
        attribution: str | None,
        entry: GtfsHubConfigEntry,
    ) -> None:
        super().__init__(coordinator, attribution)
        self._attr_device_info = service_device(entry.entry_id, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_live_data"

    @property
    @override
    def is_on(self) -> bool:
        """Return whether departures use fresh realtime data."""
        return self.coordinator.data.live
