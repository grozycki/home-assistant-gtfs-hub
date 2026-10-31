"""Actions of the integration."""

import probatio as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from gtfs_hub import GtfsError

from .const import CONF_ENTRY_ID, CONF_LIMIT, CONF_STOP_KEY, DOMAIN
from .entity import departure_as_dict

_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTRY_ID): cv.string,
        vol.Required(CONF_STOP_KEY): cv.string,
        vol.Optional(CONF_LIMIT, default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""

    async def async_get_departures(call: ServiceCall) -> ServiceResponse:
        entry = service.async_get_config_entry(hass, DOMAIN, call.data[CONF_ENTRY_ID])
        runtime = entry.runtime_data
        key = call.data[CONF_STOP_KEY]
        schedule = runtime.schedule.data
        if schedule is None or key not in schedule.stops:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_stop",
                translation_placeholders={"stop": key},
            )
        try:
            departures = await runtime.hub.async_departures(
                key, realtime=runtime.departures.realtime, limit=call.data[CONF_LIMIT]
            )
        except GtfsError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_schedule"
            ) from err
        return {"departures": [departure_as_dict(item) for item in departures]}

    hass.services.async_register(
        DOMAIN,
        "get_departures",
        async_get_departures,
        schema=_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
