"""Find a stop added in the configuration from its device, for triggers and conditions."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import probatio as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from gtfs_hub import Stop, StopKey

from .const import CONF_RADIUS, CONF_STOP, CONF_STOP_KEY, DOMAIN, MODEL_STOP, SUBENTRY_STOP
from .entity import departure_as_dict
from .presence import DEFAULT_RADIUS, MAX_RADIUS, MIN_RADIUS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.util.json import JsonValueType

    from .coordinator import GtfsHubConfigEntry

_LOGGER = logging.getLogger(__name__)

# What stop triggers and conditions target, and the options they share.
TARGET_DOMAINS = frozenset({"person", "device_tracker"})
STOP_OPTIONS = {
    vol.Required(CONF_STOP): cv.string,
    vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS): vol.All(
        vol.Coerce(float), vol.Range(min=MIN_RADIUS, max=MAX_RADIUS)
    ),
}


@dataclass(frozen=True, slots=True)
class PinnedStop:
    """A stop added in the configuration, with a known position."""

    entry: GtfsHubConfigEntry
    stop: Stop
    latitude: float
    longitude: float

    def describe(self) -> dict[str, JsonValueType]:
        """Return what an automation needs to name the stop."""
        return {"key": self.stop.key, "name": self.stop.name, "code": self.stop.code}

    def departures(self) -> list[JsonValueType]:
        """Return the stop's departures of the last tick, as the departure sensor lists them."""
        coordinator = self.entry.runtime_data.departures
        return [
            departure_as_dict(item, coordinator.badge(item.line))
            for item in coordinator.data.departures.get(self.stop.key, [])
        ]


def is_stop_device(hass: HomeAssistant, device_id: str) -> bool:
    """Return whether a device is a stop of the integration, loaded or not."""
    device = dr.async_get(hass).async_get(device_id)
    return (
        isinstance(device, dr.DeviceEntry)
        and device.model_id == MODEL_STOP
        and any(domain == DOMAIN for domain, _ in device.identifiers)
    )


def find_pinned_stop(hass: HomeAssistant, device_id: str) -> PinnedStop | None:
    """Return the stop of a device, or None when it is gone, not a stop, or not loaded."""
    device = dr.async_get(hass).async_get(device_id)
    if not isinstance(device, dr.DeviceEntry) or device.model_id != MODEL_STOP:
        return None
    identifiers = {value for domain, value in device.identifiers if domain == DOMAIN}
    for entry_id in device.config_entries:
        entry: GtfsHubConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            continue
        for subentry in entry.get_subentries_of_type(SUBENTRY_STOP):
            if subentry.subentry_id not in identifiers:
                continue
            stop = entry.runtime_data.schedule.data.stops.get(StopKey(subentry.data[CONF_STOP_KEY]))
            # Before the first schedule sync, nothing says where the stop is.
            if stop is None or stop.latitude is None or stop.longitude is None:
                return None
            return PinnedStop(entry, stop, stop.latitude, stop.longitude)
    return None


class StopLookup:
    """Finds the stop of a trigger or condition, warning once when it is gone.

    An entry that is only not loaded yet is not warned about, as that happens at every start.
    """

    def __init__(self, hass: HomeAssistant, device_id: str, consequence: str) -> None:
        self._hass = hass
        self.device_id = device_id
        self._consequence = consequence
        self._warned = False

    def __call__(self) -> PinnedStop | None:
        """Return the stop, or None when it cannot be used now."""
        stop = find_pinned_stop(self._hass, self.device_id)
        if stop is None and not self._warned and not is_stop_device(self._hass, self.device_id):
            _LOGGER.warning(
                "Device %s is not a stop of GTFS Hub, so %s", self.device_id, self._consequence
            )
            self._warned = True
        return stop
