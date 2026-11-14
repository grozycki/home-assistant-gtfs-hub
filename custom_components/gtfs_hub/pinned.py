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
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.util.json import JsonValueType

    from .coordinator import GtfsHubConfigEntry

_LOGGER = logging.getLogger(__name__)

# What stop triggers and conditions target, and the options they share.
TARGET_DOMAINS = frozenset({"person", "device_tracker"})


def _without_empty(device_ids: list[str]) -> list[str]:
    """Drop empty IDs, as an emptied picker may save an empty string."""
    return [device_id for device_id in device_ids if device_id]


# No stop at all means every pinned stop. A single string, as saved before, is a list of one.
STOP_OPTIONS = {
    vol.Optional(CONF_STOP, default=list): vol.All(cv.ensure_list, [cv.string], _without_empty),
    vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS): vol.All(
        vol.Coerce(float), vol.Range(min=MIN_RADIUS, max=MAX_RADIUS)
    ),
}


@dataclass(frozen=True, slots=True)
class PinnedStop:
    """A stop added in the configuration, with a known position."""

    entry: GtfsHubConfigEntry
    # The stop subentry's ID, unique across entries where a stop key is not.
    id: str
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


def _pinned(entry: GtfsHubConfigEntry, subentry: ConfigSubentry) -> PinnedStop | None:
    """Return a stop subentry of a loaded entry, or None before its first schedule sync."""
    stop = entry.runtime_data.schedule.data.stops.get(StopKey(subentry.data[CONF_STOP_KEY]))
    if stop is None or stop.latitude is None or stop.longitude is None:
        return None
    return PinnedStop(entry, subentry.subentry_id, stop, stop.latitude, stop.longitude)


def _loaded_entries(hass: HomeAssistant) -> list[GtfsHubConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def pinned_stops(hass: HomeAssistant) -> list[PinnedStop]:
    """Return every pinned stop of every loaded entry that has a known position."""
    return [
        pinned
        for entry in _loaded_entries(hass)
        for subentry in entry.get_subentries_of_type(SUBENTRY_STOP)
        if (pinned := _pinned(entry, subentry)) is not None
    ]


def find_pinned_stop(hass: HomeAssistant, device_id: str) -> PinnedStop | None:
    """Return the stop of a device, or None when it is gone, not a stop, or not loaded."""
    device = dr.async_get(hass).async_get(device_id)
    if not isinstance(device, dr.DeviceEntry) or device.model_id != MODEL_STOP:
        return None
    identifiers = {value for domain, value in device.identifiers if domain == DOMAIN}
    for entry in _loaded_entries(hass):
        if entry.entry_id not in device.config_entries:
            continue
        for subentry in entry.get_subentries_of_type(SUBENTRY_STOP):
            if subentry.subentry_id in identifiers:
                return _pinned(entry, subentry)
    return None


class StopsLookup:
    """Finds the stops of a trigger or condition, warning once for each one that is gone.

    No device means every pinned stop. An entry that is only not loaded yet is not
    warned about, as that happens at every start.
    """

    def __init__(self, hass: HomeAssistant, device_ids: list[str], consequence: str) -> None:
        self._hass = hass
        # Picking a stop twice counts it once.
        self._device_ids = list(dict.fromkeys(device_ids))
        self._consequence = consequence
        self._warned: set[str] = set()

    def __call__(self) -> list[PinnedStop]:
        """Return the stops that can be used now."""
        if not self._device_ids:
            return pinned_stops(self._hass)
        stops: list[PinnedStop] = []
        for device_id in self._device_ids:
            if (stop := find_pinned_stop(self._hass, device_id)) is not None:
                stops.append(stop)
            elif device_id not in self._warned and not is_stop_device(self._hass, device_id):
                _LOGGER.warning(
                    "Device %s is not a stop of GTFS Hub, so %s", device_id, self._consequence
                )
                self._warned.add(device_id)
        return stops
