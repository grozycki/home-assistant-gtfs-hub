"""The GTFS Hub integration."""

from dataclasses import dataclass
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

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
from .coordinator import DeparturesCoordinator, ScheduleCoordinator
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class GtfsHubRuntime:
    """What one entry keeps while it is loaded."""

    provider: Provider
    hub: GtfsHub
    schedule: ScheduleCoordinator
    departures: DeparturesCoordinator


type GtfsHubConfigEntry = ConfigEntry[GtfsHubRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""
    async_setup_services(hass)
    return True


async def async_build_provider(data: dict[str, str]) -> Provider:
    """Create the provider an entry names."""
    if data[CONF_PROVIDER] == GENERIC_PROVIDER:
        return GenericProvider(
            [data[CONF_SCHEDULE_URL]], trip_updates_url=data.get(CONF_TRIP_UPDATES_URL)
        )
    return await async_create_provider(data[CONF_PROVIDER])


def _database_path(hass: HomeAssistant, entry: ConfigEntry) -> Path:
    return Path(hass.config.cache_path(DOMAIN, f"{entry.entry_id}.db"))


async def async_setup_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Set up an entry, leaving the first import to run in the background."""
    try:
        provider = await async_build_provider(dict(entry.data))
    except GtfsError as err:
        raise ConfigEntryError(f"Provider unavailable: {err}") from err
    hub = GtfsHub(provider, _database_path(hass, entry), async_get_clientsession(hass))
    schedule = ScheduleCoordinator(hass, entry, hub)
    departures = DeparturesCoordinator(hass, entry, hub, schedule)
    entry.runtime_data = GtfsHubRuntime(provider, hub, schedule, departures)

    @callback
    def _schedule_changed() -> None:
        hass.async_create_task(departures.async_request_refresh())

    # A coordinator polls only with a listener, and this one also follows new stops.
    entry.async_on_unload(schedule.async_add_listener(_schedule_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    entry.async_create_background_task(hass, schedule.async_refresh(), f"{DOMAIN}_first_sync")
    return True


async def _async_reload(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GtfsHubConfigEntry) -> bool:
    """Unload an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the timetable cache of a removed entry."""
    path = _database_path(hass, entry)
    await hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
