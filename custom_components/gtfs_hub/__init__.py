"""The GTFS Hub integration."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from gtfs_hub import (
    GenericProvider,
    GtfsError,
    GtfsHub,
    LineKey,
    Provider,
    async_create_provider,
    delete_store,
)

from .const import (
    CONF_PROVIDER,
    CONF_ROUTE_TYPE,
    CONF_SCHEDULE_URL,
    CONF_SHORT_NAME,
    CONF_TRIP_UPDATES_URL,
    DOMAIN,
    GENERIC_PROVIDER,
    MODEL_LINE,
    PLATFORMS,
    SUBENTRY_LINE,
)
from .coordinator import (
    DeparturesCoordinator,
    GtfsHubConfigEntry,
    GtfsHubRuntime,
    ScheduleCoordinator,
)
from .entity import service_device
from .marker import MarkerView, StopMarkerView
from .recorder_load import async_clear_recorder_issue, tracker_entity_id, tracker_prefix
from .services import async_setup_services
from .sightings import async_remove_sightings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    hass.http.register_view(MarkerView())
    hass.http.register_view(StopMarkerView())
    return True


async def async_build_provider(data: Mapping[str, str]) -> Provider:
    """Create the provider an entry names."""
    if data[CONF_PROVIDER] == GENERIC_PROVIDER:
        return GenericProvider(
            [data[CONF_SCHEDULE_URL]], trip_updates_url=data.get(CONF_TRIP_UPDATES_URL)
        )
    return await async_create_provider(data[CONF_PROVIDER])


def database_path(hass: HomeAssistant, unique_id: str) -> Path:
    """Return the database of a source, known before its entry exists."""
    name = hashlib.sha256(unique_id.encode()).hexdigest()[:16]
    return Path(hass.config.cache_path(DOMAIN, f"{name}.db"))


async def async_migrate_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Give trackers made before minor version 2 their prefixed entity IDs, once.

    Home Assistant itself refuses entries of a newer major version.
    """
    if entry.minor_version < 2:
        _rename_trackers(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


def _rename_trackers(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    registry = er.async_get(hass)
    provider = entry.data[CONF_PROVIDER]
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain != "device_tracker" or item.entity_id.startswith(tracker_prefix(provider)):
            continue
        # The original name is "11 #1347", or "11 <key>" without a fleet number.
        line, _, number = (item.original_name or "").partition(" ")
        if not line or not number:
            continue
        target = tracker_entity_id(provider, line, number.removeprefix("#"))
        if registry.async_get(target) is None:
            registry.async_update_entity(item.entity_id, new_entity_id=target)


async def async_setup_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Set up an entry, leaving the first import to run in the background."""
    try:
        provider = await async_build_provider(entry.data)
    except GtfsError as err:
        raise ConfigEntryError(f"Provider unavailable: {err}") from err
    assert entry.unique_id is not None
    hub = GtfsHub(provider, database_path(hass, entry.unique_id), async_get_clientsession(hass))
    schedule = ScheduleCoordinator(hass, entry, hub)
    departures = DeparturesCoordinator(hass, entry, hub, schedule)
    entry.runtime_data = GtfsHubRuntime(provider, hub, schedule, departures)

    @callback
    def _schedule_changed() -> None:
        _update_line_devices(hass, entry)
        _ = entry.async_create_background_task(
            hass, departures.async_request_refresh(), f"{DOMAIN}_departures_refresh"
        )

    # A coordinator polls only with a listener, and this one also follows new stops.
    entry.async_on_unload(schedule.async_add_listener(_schedule_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _ = entry.async_create_background_task(hass, schedule.async_refresh(), f"{DOMAIN}_first_sync")
    return True


@callback
def _update_line_devices(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    """Keep a device per tracked line, linked to its timetable when the source has one."""
    registry = dr.async_get(hass)
    lines = entry.runtime_data.schedule.data.lines
    for subentry in entry.get_subentries_of_type(SUBENTRY_LINE):
        line = lines.get(LineKey(subentry.data[CONF_ROUTE_TYPE], subentry.data[CONF_SHORT_NAME]))
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry.subentry_id,
            configuration_url=line.url if line is not None else None,
            # The delay event describes the same device, so both take it from one place.
            **service_device(subentry.subentry_id, subentry.title, MODEL_LINE),
        )


async def _async_reload(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    if not entry.runtime_data.applying:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Unload an entry. Its trackers write nothing while it is unloaded, so the issue goes."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_clear_recorder_issue(hass, entry)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    """Delete the timetable cache and kept schedule files of a removed entry."""
    assert entry.unique_id is not None
    path = database_path(hass, entry.unique_id)
    await hass.async_add_executor_job(delete_store, path)
    await async_remove_sightings(hass, entry.entry_id)
