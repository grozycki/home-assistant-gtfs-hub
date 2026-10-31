"""Config, subentry and options flows."""

from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from gtfs_hub import (
    GtfsConnectionError,
    GtfsError,
    GtfsFeedError,
    GtfsNoDataError,
    Line,
    Stop,
    async_list_provider_keys,
)

from . import async_build_provider
from .const import (
    CONF_LINE,
    CONF_PERSONS,
    CONF_PROVIDER,
    CONF_QUERY,
    CONF_ROUTE_TYPE,
    CONF_SCHEDULE_URL,
    CONF_SHORT_NAME,
    CONF_STOP,
    CONF_STOP_KEY,
    CONF_TRIP_UPDATES_URL,
    DOMAIN,
    GENERIC_PROVIDER,
    SUBENTRY_LINE,
    SUBENTRY_STOP,
)

_MODE_NAMES = {0: "Tram", 3: "Bus", 11: "Trolleybus"}
_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _line_label(line: Line) -> str:
    mode = _MODE_NAMES.get(line.key.route_type, f"Type {line.key.route_type}")
    name = f"{mode} {line.key.short_name}"
    return f"{name}: {line.long_name}" if line.long_name else name


def _stop_label(stop: Stop) -> str:
    return f"{stop.name} ({stop.code})" if stop.code else stop.name


class GtfsHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a provider."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the repeatable parts of an entry."""
        return {SUBENTRY_LINE: LineSubentryFlow, SUBENTRY_STOP: StopSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GtfsHubOptionsFlow()

    async def _async_validate(self, data: dict[str, str]) -> tuple[str | None, str]:
        """Return an error key, and the provider's display name."""
        try:
            provider = await async_build_provider(data)
            await provider.async_list_static_feeds(async_get_clientsession(self.hass))
        except GtfsConnectionError:
            return "cannot_connect", ""
        except GtfsFeedError:
            return "invalid_feed", ""
        except GtfsError:
            return "unknown", ""
        return None, provider.name

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a provider."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_PROVIDER]
            if key == GENERIC_PROVIDER:
                return await self.async_step_generic()
            await self.async_set_unique_id(key)
            self._abort_if_unique_id_configured()
            error, name = await self._async_validate({CONF_PROVIDER: key})
            if error is None:
                return self.async_create_entry(title=name, data={CONF_PROVIDER: key})
            errors["base"] = error
        keys = [*await async_list_provider_keys(), GENERIC_PROVIDER]
        schema = vol.Schema(
            {
                vol.Required(CONF_PROVIDER): SelectSelector(
                    SelectSelectorConfig(options=keys, translation_key=CONF_PROVIDER)
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_generic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up a source from URLs."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_SCHEDULE_URL]
            await self.async_set_unique_id(f"{GENERIC_PROVIDER}_{url}")
            self._abort_if_unique_id_configured()
            data = {CONF_PROVIDER: GENERIC_PROVIDER, **user_input}
            error, name = await self._async_validate(data)
            if error is None:
                return self.async_create_entry(title=name, data=data)
            errors["base"] = error
        schema = vol.Schema(
            {vol.Required(CONF_SCHEDULE_URL): _URL, vol.Optional(CONF_TRIP_UPDATES_URL): _URL}
        )
        return self.async_show_form(step_id="generic", data_schema=schema, errors=errors)


class _SearchFlow(ConfigSubentryFlow):
    """Search first, as thousands of options fit no dropdown."""

    _options: list[SelectOptionDict]

    def _runtime(self) -> Any:
        entry = self._get_entry()
        return entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    def _query_form(self, errors: dict[str, str] | None = None) -> SubentryFlowResult:
        schema = vol.Schema({vol.Required(CONF_QUERY): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors or {})

    def _pick_form(self, field: str) -> SubentryFlowResult:
        selector = SelectSelector(SelectSelectorConfig(options=self._options))
        return self.async_show_form(
            step_id="pick", data_schema=vol.Schema({vol.Required(field): selector})
        )


class LineSubentryFlow(_SearchFlow):
    """Add a line to follow."""

    _lines: dict[str, Line]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Search for a line."""
        runtime = self._runtime()
        if runtime is None:
            return self.async_abort(reason="entry_not_loaded")
        if user_input is None:
            return self._query_form()
        try:
            lines = await runtime.hub.async_search_lines(user_input[CONF_QUERY])
        except GtfsNoDataError:
            return self.async_abort(reason="catalogue_not_ready")
        if not lines:
            return self._query_form({"base": "no_matches"})
        self._lines = {f"{line.key.route_type}:{line.key.short_name}": line for line in lines}
        self._options = [
            SelectOptionDict(value=key, label=_line_label(line))
            for key, line in self._lines.items()
        ]
        return self._pick_form(CONF_LINE)

    async def async_step_pick(self, user_input: dict[str, Any]) -> SubentryFlowResult:
        """Add the chosen line."""
        key = user_input[CONF_LINE]
        for subentry in self._get_entry().get_subentries_of_type(SUBENTRY_LINE):
            if subentry.unique_id == key:
                return self.async_abort(reason="already_configured")
        line = self._lines[key]
        return self.async_create_entry(
            title=_line_label(line),
            data={CONF_ROUTE_TYPE: line.key.route_type, CONF_SHORT_NAME: line.key.short_name},
            unique_id=key,
        )


class StopSubentryFlow(_SearchFlow):
    """Pin a stop of the selected lines."""

    _stops: dict[str, Stop]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Search among stops of the selected lines."""
        runtime = self._runtime()
        if runtime is None:
            return self.async_abort(reason="entry_not_loaded")
        schedule = runtime.schedule.data
        if schedule is None or not schedule.stops:
            return self.async_abort(reason="no_lines")
        if user_input is None:
            return self._query_form()
        found = await runtime.hub.async_search_stops(
            user_input[CONF_QUERY], limit=50, only=schedule.stops.keys()
        )
        self._stops = {stop.key: stop for stop in found}
        if not self._stops:
            return self._query_form({"base": "no_matches"})
        self._options = [
            SelectOptionDict(value=key, label=_stop_label(stop))
            for key, stop in self._stops.items()
        ]
        return self._pick_form(CONF_STOP)

    async def async_step_pick(self, user_input: dict[str, Any]) -> SubentryFlowResult:
        """Pin the chosen stop."""
        key = user_input[CONF_STOP]
        for subentry in self._get_entry().get_subentries_of_type(SUBENTRY_STOP):
            if subentry.unique_id == key:
                return self.async_abort(reason="already_configured")
        return self.async_create_entry(
            title=_stop_label(self._stops[key]), data={CONF_STOP_KEY: key}, unique_id=key
        )


class GtfsHubOptionsFlow(OptionsFlow):
    """Choose persons for the nearest stop sensor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the persons."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PERSONS, default=self.config_entry.options.get(CONF_PERSONS, [])
                ): EntitySelector(EntitySelectorConfig(domain="person", multiple=True))
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
