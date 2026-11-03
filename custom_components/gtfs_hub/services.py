"""Actions of the integration."""

import contextlib
from typing import TYPE_CHECKING

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
from homeassistant.util import dt as dt_util

from gtfs_hub import GtfsError, LineKey, Shape, StopKey

from .const import (
    CONF_ENTRY_ID,
    CONF_LIMIT,
    CONF_LINE,
    CONF_ROUTE_TYPE,
    CONF_STOP_KEY,
    DOMAIN,
)
from .entity import departure_as_dict

if TYPE_CHECKING:
    from homeassistant.util.json import JsonValueType

    from .coordinator import GtfsHubConfigEntry

_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTRY_ID): cv.string,
        vol.Required(CONF_STOP_KEY): cv.string,
        vol.Optional(CONF_LIMIT, default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    }
)

_SHAPES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTRY_ID): cv.string,
        vol.Required(CONF_LINE): cv.string,
        vol.Optional(CONF_ROUTE_TYPE): vol.Coerce(int),
    }
)


def _selected_line(entry: GtfsHubConfigEntry, name: str, route_type: int | None) -> LineKey:
    """Find a selected line by short name, which two modes may share."""
    found = [
        key
        for key in entry.runtime_data.schedule.selection()
        if key.short_name == name and route_type in (None, key.route_type)
    ]
    if not found:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_line",
            translation_placeholders={"line": name},
        )
    if len(found) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ambiguous_line",
            translation_placeholders={"line": name},
        )
    return found[0]


def _shape_as_dict(shape: Shape, color: str | None) -> dict[str, JsonValueType]:
    """Return a shape in JSON-safe form, with points as latitude and longitude pairs."""
    points: list[JsonValueType] = [[latitude, longitude] for latitude, longitude in shape.points]
    return {
        "id": shape.id,
        "headsign": shape.headsign,
        "direction_id": shape.direction_id,
        # Some feeds write the hash that GTFS leaves out.
        "color": None if color is None else f"#{color.removeprefix('#')}",
        "points": points,
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""

    async def async_get_departures(call: ServiceCall) -> ServiceResponse:
        entry: GtfsHubConfigEntry = service.async_get_config_entry(
            hass, DOMAIN, call.data[CONF_ENTRY_ID]
        )
        runtime = entry.runtime_data
        key = StopKey(call.data[CONF_STOP_KEY])
        if key not in runtime.schedule.data.stops:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_stop",
                translation_placeholders={"stop": key},
            )
        # With nothing pinned, the coordinator does not poll, so fetch for this call.
        realtime = runtime.departures.realtime
        if realtime is None:
            with contextlib.suppress(GtfsError):
                realtime = await runtime.hub.async_fetch_realtime()
        try:
            departures = await runtime.hub.async_departures(
                key, realtime=realtime, limit=call.data[CONF_LIMIT]
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

    async def async_get_line_shapes(call: ServiceCall) -> ServiceResponse:
        entry: GtfsHubConfigEntry = service.async_get_config_entry(
            hass, DOMAIN, call.data[CONF_ENTRY_ID]
        )
        key = _selected_line(entry, call.data[CONF_LINE], call.data.get(CONF_ROUTE_TYPE))
        try:
            # Today's paths only, so other days' variants are not drawn beside them.
            shapes = await entry.runtime_data.hub.async_line_shapes(key, dt_util.now().date())
        except GtfsError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="no_schedule"
            ) from err
        line = entry.runtime_data.schedule.data.lines.get(key)
        color = line.color if line is not None else None
        return {"shapes": [_shape_as_dict(shape, color) for shape in shapes]}

    hass.services.async_register(
        DOMAIN,
        "get_line_shapes",
        async_get_line_shapes,
        schema=_SHAPES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
