"""The GTFS Hub integration."""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from gtfs_hub import (
    GenericProvider,
    GtfsError,
    GtfsHub,
    Provider,
    async_create_provider,
)

from .const import (
    CONF_PROVIDER,
    CONF_SCHEDULE_URL,
    CONF_TRIP_UPDATES_URL,
    DOMAIN,
    GENERIC_PROVIDER,
    PLATFORMS,
)
from .coordinator import (
    DeparturesCoordinator,
    GtfsHubConfigEntry,
    GtfsHubRuntime,
    ScheduleCoordinator,
)
from .marker import MarkerView, StopMarkerView
from .services import async_setup_services

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
        _ = entry.async_create_background_task(
            hass, departures.async_request_refresh(), f"{DOMAIN}_departures_refresh"
        )

    # A coordinator polls only with a listener, and this one also follows new stops.
    entry.async_on_unload(schedule.async_add_listener(_schedule_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _ = entry.async_create_background_task(hass, schedule.async_refresh(), f"{DOMAIN}_first_sync")
    return True


async def _async_reload(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    if not entry.runtime_data.applying:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Unload an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    """Delete the timetable cache of a removed entry."""
    assert entry.unique_id is not None
    path = database_path(hass, entry.unique_id)
    await hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
