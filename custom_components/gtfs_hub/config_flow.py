"""Config and options flows: source, lines, stops, persons."""

import logging
from abc import abstractmethod
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, override

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryBaseFlow,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from gtfs_hub import (
    GtfsConnectionError,
    GtfsError,
    GtfsFeedError,
    GtfsHub,
    Line,
    LineKey,
    Provider,
    StopChoice,
    async_list_provider_keys,
)

from . import async_build_provider, database_path
from .const import (
    CONF_PERSONS,
    CONF_PROVIDER,
    CONF_ROUTE_TYPE,
    CONF_SCHEDULE_URL,
    CONF_SHORT_NAME,
    CONF_STOP_KEY,
    CONF_STOPS,
    CONF_TRIP_UPDATES_URL,
    DOMAIN,
    GENERIC_PROVIDER,
    SUBENTRY_LINE,
    SUBENTRY_STOP,
)
from .marker import mode_of

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Coroutine, Sequence
    from pathlib import Path

    from .coordinator import GtfsHubConfigEntry

_LOGGER = logging.getLogger(__name__)
_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_MODES = ("bus", "tram", "trolleybus", "other")


def _line_value(key: LineKey) -> str:
    return f"{key.route_type}:{key.short_name}"


def _line_label(line: Line) -> str:
    name = line.key.short_name
    return f"{name}: {line.long_name}" if line.long_name else name


def _stop_title(choice: StopChoice) -> str:
    """Return a label telling a stop from others of the same name."""
    direction = f" → {', '.join(choice.headsigns)}" if choice.headsigns else ""
    code = f" [{choice.stop.code}]" if choice.stop.code else ""
    return f"{choice.stop.name}{direction} ({', '.join(choice.lines)}){code}"


def _stop_device_title(choice: StopChoice) -> str:
    """Return a shorter name, as devices and entity IDs are built from it."""
    direction = f" → {choice.headsigns[0]}" if len(choice.headsigns) == 1 else ""
    return f"{choice.stop.name}{direction}"


def _subentry_title(line: Line) -> str:
    return _line_label(line)


def _line_subentry_data(line: Line) -> dict[str, str | int]:
    return {CONF_ROUTE_TYPE: line.key.route_type, CONF_SHORT_NAME: line.key.short_name}


class _SelectionFlow(ConfigEntryBaseFlow):
    """The steps setup and options share."""

    _task: asyncio.Task[None] | None = None
    _lines: list[Line]
    _chosen: list[Line]
    _choices: list[StopChoice]
    _stops: list[str]

    @abstractmethod
    def _hub(self) -> GtfsHub:
        """Return the hub this flow works on."""

    @abstractmethod
    def _current_lines(self) -> list[str]:
        """Return the line values to fill in."""

    @abstractmethod
    def _current_stops(self) -> list[str]:
        """Return the stop keys to fill in."""

    @abstractmethod
    def _current_persons(self) -> list[str]:
        """Return the persons to fill in."""

    @abstractmethod
    async def _async_finish(self, persons: list[str]) -> ConfigFlowResult:
        """Apply the choices."""

    async def _async_load_lines(self) -> None:
        await self._hub().async_sync([])
        self._lines = await self._hub().async_list_lines()

    async def _async_load_stops(self) -> None:
        keys = [line.key for line in self._chosen]
        await self._hub().async_sync(keys)
        self._choices = await self._hub().async_stop_choices(keys)

    async def _async_progress(
        self, step: str, load: Callable[[], Coroutine[None, None, None]], next_step: str
    ) -> ConfigFlowResult:
        """Run a download behind a progress step."""
        started = self._task is None
        job = self._task if self._task is not None else self.hass.async_create_task(load())
        self._task = job
        if not job.done():
            return self.async_show_progress(step_id=step, progress_action=step, progress_task=job)
        self._task = None
        failed = job.cancelled() or job.exception() is not None
        if failed:
            _LOGGER.warning(
                "Schedule download failed: %s", None if job.cancelled() else job.exception()
            )
        target = "failed" if failed else next_step
        # The manager passes a step's input on to the next, so a task that ended at once,
        # with the previous form's input still in hand, must not go through it.
        if started:
            handler: Callable[[], Awaitable[ConfigFlowResult]] = getattr(
                self, f"async_step_{target}"
            )
            return await handler()
        return self.async_show_progress_done(next_step_id=target)

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """End the flow after a failed download."""
        return self.async_abort(reason="download_failed")

    async def async_step_lines(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose lines, one field per mode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = {value for mode in _MODES for value in user_input.get(mode, [])}
            self._chosen = [line for line in self._lines if _line_value(line.key) in chosen]
            if self._chosen:
                return await self.async_step_timetables()
            errors["base"] = "no_lines"
        current = self._current_lines()
        fields: dict[vol.Marker, SelectSelector] = {}
        for mode in _MODES:
            lines = [line for line in self._lines if mode_of(line.key.route_type) == mode]
            if not lines:
                continue
            options = [
                SelectOptionDict(value=_line_value(line.key), label=_line_label(line))
                for line in lines
            ]
            values = {option["value"] for option in options}
            fields[
                vol.Optional(
                    mode, description={"suggested_value": [v for v in current if v in values]}
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    sort=False,
                )
            )
        return self.async_show_form(step_id="lines", data_schema=vol.Schema(fields), errors=errors)

    async def async_step_timetables(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download timetables of the chosen lines."""
        return await self._async_progress("timetables", self._async_load_stops, "stops")

    async def async_step_stops(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose stops of the chosen lines."""
        if user_input is not None:
            self._stops = user_input.get(CONF_STOPS, [])
            return await self.async_step_persons()
        options = [
            SelectOptionDict(value=str(choice.stop.key), label=_stop_title(choice))
            for choice in self._choices
        ]
        values = {option["value"] for option in options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_STOPS,
                    description={
                        "suggested_value": [v for v in self._current_stops() if v in values]
                    },
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        sort=False,
                    )
                )
            }
        )
        return self.async_show_form(step_id="stops", data_schema=schema)

    async def async_step_persons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose persons for the nearest stop sensor."""
        if user_input is not None:
            return await self._async_finish(user_input.get(CONF_PERSONS, []))
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PERSONS, description={"suggested_value": self._current_persons()}
                ): EntitySelector(EntitySelectorConfig(domain="person", multiple=True))
            }
        )
        return self.async_show_form(step_id="persons", data_schema=schema)

    def _stop_title_of(self, key: str) -> str:
        return next(_stop_device_title(c) for c in self._choices if c.stop.key == key)


class GtfsHubConfigFlow(_SelectionFlow, ConfigFlow, domain=DOMAIN):
    """Add a source and choose what to follow."""

    VERSION = 1

    _flow_hub: GtfsHub
    _provider: Provider
    _data: dict[str, str]
    _created = False

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GtfsHubOptionsFlow()

    @override
    def _hub(self) -> GtfsHub:
        return self._flow_hub

    @override
    def _current_lines(self) -> list[str]:
        return []

    @override
    def _current_stops(self) -> list[str]:
        return []

    @override
    def _current_persons(self) -> list[str]:
        return []

    def _path(self) -> Path:
        assert self.unique_id is not None
        return database_path(self.hass, self.unique_id)

    async def _async_validate(self, data: dict[str, str]) -> str | None:
        """Return an error key, keeping the provider on success."""
        session = async_get_clientsession(self.hass)
        try:
            provider = await async_build_provider(data)
            await provider.async_list_static_feeds(session)
        except GtfsConnectionError:
            return "cannot_connect"
        except GtfsFeedError:
            return "invalid_feed"
        except GtfsError:
            return "unknown"
        self._provider = provider
        self._data = data
        self._flow_hub = GtfsHub(provider, self._path(), session)
        return None

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose a source."""
        errors: dict[str, str] = {}
        if user_input is not None:
            key = user_input[CONF_PROVIDER]
            if key == GENERIC_PROVIDER:
                return await self.async_step_generic()
            await self.async_set_unique_id(key)
            self._abort_if_unique_id_configured()
            error = await self._async_validate({CONF_PROVIDER: key})
            if error is None:
                return await self.async_step_catalogue()
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
            await self.async_set_unique_id(f"{GENERIC_PROVIDER}_{user_input[CONF_SCHEDULE_URL]}")
            self._abort_if_unique_id_configured()
            error = await self._async_validate({CONF_PROVIDER: GENERIC_PROVIDER, **user_input})
            if error is None:
                return await self.async_step_catalogue()
            errors["base"] = error
        schema = vol.Schema(
            {vol.Required(CONF_SCHEDULE_URL): _URL, vol.Optional(CONF_TRIP_UPDATES_URL): _URL}
        )
        return self.async_show_form(step_id="generic", data_schema=schema, errors=errors)

    async def async_step_catalogue(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Download the list of lines."""
        return await self._async_progress("catalogue", self._async_load_lines, "lines")

    @override
    async def _async_finish(self, persons: list[str]) -> ConfigFlowResult:
        subentries: Sequence[ConfigSubentryData] = [
            ConfigSubentryData(
                data=_line_subentry_data(line),
                subentry_type=SUBENTRY_LINE,
                title=_subentry_title(line),
                unique_id=_line_value(line.key),
            )
            for line in self._chosen
        ] + [
            ConfigSubentryData(
                data={CONF_STOP_KEY: key},
                subentry_type=SUBENTRY_STOP,
                title=self._stop_title_of(key),
                unique_id=key,
            )
            for key in self._stops
        ]
        self._created = True
        return self.async_create_entry(
            title=self._provider.name,
            data=self._data,
            options={CONF_PERSONS: persons},
            subentries=subentries,
        )

    @callback
    @override
    def async_remove(self) -> None:
        """Delete what an abandoned setup downloaded."""
        job = self._task
        if self.unique_id is None or self._created or not hasattr(self, "_flow_hub"):
            if job is not None:
                job.cancel()
            return
        path = self._path()

        @callback
        def delete(_: object = None) -> None:
            _ = self.hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))

        if job is None:
            delete()
        else:
            # A running download could still swap its file in, so delete after it.
            job.add_done_callback(delete)
            job.cancel()


class GtfsHubOptionsFlow(_SelectionFlow, OptionsFlow):
    """Change what an entry follows."""

    @property
    def _entry(self) -> GtfsHubConfigEntry:
        return self.config_entry

    @override
    def _hub(self) -> GtfsHub:
        return self._entry.runtime_data.hub

    @override
    def _current_lines(self) -> list[str]:
        return [
            _line_value(LineKey(item.data[CONF_ROUTE_TYPE], item.data[CONF_SHORT_NAME]))
            for item in self._entry.get_subentries_of_type(SUBENTRY_LINE)
        ]

    @override
    def _current_stops(self) -> list[str]:
        return [
            item.data[CONF_STOP_KEY] for item in self._entry.get_subentries_of_type(SUBENTRY_STOP)
        ]

    @override
    def _current_persons(self) -> list[str]:
        persons: list[str] = self._entry.options.get(CONF_PERSONS, [])
        return persons

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Read the lines, then show them."""
        if self._entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")
        try:
            self._lines = await self._hub().async_list_lines()
        except GtfsError:
            return self.async_abort(reason="download_failed")
        return await self.async_step_lines()

    @override
    async def _async_finish(self, persons: list[str]) -> ConfigFlowResult:
        entry = self._entry
        manager = self.hass.config_entries
        wanted: dict[str, tuple[str, dict[str, str | int], str]] = {
            _line_value(line.key): (
                SUBENTRY_LINE,
                _line_subentry_data(line),
                _subentry_title(line),
            )
            for line in self._chosen
        }
        wanted.update(
            {
                key: (SUBENTRY_STOP, {CONF_STOP_KEY: key}, self._stop_title_of(key))
                for key in self._stops
            }
        )
        # Each change would reload the entry, so reload once, after the last.
        entry.runtime_data.applying = True
        for subentry in list(entry.subentries.values()):
            if subentry.unique_id not in wanted:
                manager.async_remove_subentry(entry, subentry.subentry_id)
        known = {subentry.unique_id for subentry in entry.subentries.values()}
        for key, (kind, data, title) in wanted.items():
            if key not in known:
                manager.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(data),
                        subentry_type=kind,
                        title=title,
                        unique_id=key,
                    ),
                )
        entry.runtime_data.applying = False
        # New options reload the entry through its listener, so only same ones need this.
        if persons == self._current_persons():
            manager.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data={CONF_PERSONS: persons})
