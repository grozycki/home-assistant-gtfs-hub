"""Tracker entity IDs, and a repair issue when the recorder stores many trackers."""

from typing import TYPE_CHECKING, Final

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import slugify

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import GtfsHubConfigEntry

# About what one busy line collects in a day. Below it the load is small.
ISSUE_THRESHOLD: Final = 20
EXCLUDE_PATTERN: Final = "device_tracker.gtfs_hub_*"
_LEARN_MORE: Final = "https://github.com/grozycki/home-assistant-gtfs-hub#recorder"


def tracker_prefix(provider: str) -> str:
    """Return the start of every tracker entity ID of a provider."""
    return f"device_tracker.gtfs_hub_{slugify(provider)}_"


def tracker_entity_id(provider: str, line: str, number: str) -> str:
    """Return the entity ID a tracker requests, from its line and fleet number or key."""
    return f"{tracker_prefix(provider)}{slugify(f'{line} {number}')}"


def _issue_id(entry: GtfsHubConfigEntry) -> str:
    return f"trackers_recorded_{entry.entry_id}"


def async_check_recorder_load(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    """Raise the issue while many trackers of an entry are recorded, and clear it otherwise."""
    trackers = [
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.domain == "device_tracker"
    ]
    if _many_recorded(hass, trackers):
        ir.async_create_issue(
            hass,
            DOMAIN,
            _issue_id(entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="trackers_recorded",
            translation_placeholders={
                "entry": entry.title,
                "count": str(len(trackers)),
                "pattern": EXCLUDE_PATTERN,
            },
            learn_more_url=_LEARN_MORE,
        )
    else:
        async_clear_recorder_issue(hass, entry)


def async_clear_recorder_issue(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    """Remove the issue of an entry."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry))


def _many_recorded(hass: HomeAssistant, trackers: list[str]) -> bool:
    if len(trackers) < ISSUE_THRESHOLD or "recorder" not in hass.config.components:
        return False
    # Imported only once the recorder is loaded, as it pulls in the database layer.
    from homeassistant.components.recorder import entity_options

    return any(entity_options.is_entity_recorded(hass, entity_id) for entity_id in trackers)
