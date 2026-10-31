from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

from custom_components.gtfs_hub import database_path
from custom_components.gtfs_hub.const import DOMAIN
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SelectSelector
from probatio import Schema
from pytest_homeassistant_custom_component.common import MockConfigEntry

from conftest import FakeHub, FakeProvider, line_subentry, make_entry, stop_subentry
from gtfs_hub import GtfsConnectionError, GtfsError, GtfsFeedError, LineKey

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from homeassistant.config_entries import ConfigFlowResult, ConfigSubentryData
    from homeassistant.core import HomeAssistant

BUILD = "custom_components.gtfs_hub.config_flow.async_build_provider"
HUB = "custom_components.gtfs_hub.config_flow.GtfsHub"


def field_options(result: Mapping[str, object], field: str) -> object:
    """Return the options of a select field."""
    schema = result.get("data_schema")
    assert isinstance(schema, Schema)
    selector = schema.schema[field]
    assert isinstance(selector, SelectSelector)
    return selector.config["options"]


def field_names(result: Mapping[str, object]) -> list[str]:
    schema = result.get("data_schema")
    assert isinstance(schema, Schema)
    return [str(field) for field in schema.schema]


async def _start(hass: HomeAssistant) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def _next(hass: HomeAssistant, result: ConfigFlowResult) -> ConfigFlowResult:
    """Wait for a progress step, which an instant fake may already have finished."""
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def _to_lines(hass: HomeAssistant) -> ConfigFlowResult:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"provider": "fake"})
    return await _next(hass, result)


async def test_full_setup(hass: HomeAssistant, hub: FakeHub) -> None:
    with patch(HUB, return_value=hub):
        result = await _to_lines(hass)
        assert result["step_id"] == "lines"
        assert field_names(result) == ["bus", "tram", "other"]
        assert field_options(result, "bus") == [{"value": "3:11", "label": "11: Long"}]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"bus": ["3:11"], "tram": ["0:11"]}
        )
        result = await _next(hass, result)
        assert result["step_id"] == "stops"
        assert hub.synced[-1] == [line.key for line in await hub.async_list_lines()][:2]
        assert field_options(result, "stops") == [
            {"value": "A", "label": "Alfa → North (11) [C-A]"},
            {"value": "B", "label": "Beta (11)"},
        ]
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"stops": ["A"]})
        assert result["step_id"] == "persons"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"persons": ["person.ania"]}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fake"
    assert result["options"] == {"persons": ["person.ania"], "live_interval": 30}
    await hass.async_block_till_done()
    entry = result["result"]
    assert len(entry.get_subentries_of_type("line")) == 2
    assert [item.title for item in entry.get_subentries_of_type("stop")] == ["Alfa → North"]


async def test_stops_may_stay_empty(hass: HomeAssistant, hub: FakeHub) -> None:
    with patch(HUB, return_value=hub):
        result = await _to_lines(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"bus": ["3:11"]}
        )
        result = await _next(hass, result)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_a_line_is_required(hass: HomeAssistant, hub: FakeHub) -> None:
    with patch(HUB, return_value=hub):
        result = await _to_lines(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_lines"}
    await hass.config_entries.flow.async_configure(result["flow_id"], {"bus": ["3:11"]})
    await hass.async_block_till_done()
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_download_failure_aborts(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.sync_error = GtfsConnectionError("down")
    with patch(HUB, return_value=hub):
        result = await _to_lines(hass)
    assert result["reason"] == "download_failed"


async def test_source_errors_then_recovery(hass: HomeAssistant, hub: FakeHub) -> None:
    result = await _start(hass)
    for error, key in (
        (GtfsConnectionError("x"), "cannot_connect"),
        (GtfsFeedError("x"), "invalid_feed"),
        (GtfsError("x"), "unknown"),
    ):
        with patch(BUILD, side_effect=error):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"provider": "fake"}
            )
        assert result["errors"] == {"base": key}
    with patch(BUILD, return_value=FakeProvider()), patch(HUB, return_value=hub):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"provider": "fake"}
        )
    assert result["step_id"] == "lines"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_duplicate_provider(hass: HomeAssistant, hub: FakeHub) -> None:
    make_entry().add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"provider": "fake"})
    assert result["reason"] == "already_configured"


async def test_generic_source(hass: HomeAssistant, hub: FakeHub) -> None:
    url = {"schedule_url": "https://example.test/gtfs.zip"}
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "generic"}
    )
    assert result["step_id"] == "generic"
    with patch(BUILD, side_effect=GtfsConnectionError("x")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], url)
    assert result["errors"] == {"base": "cannot_connect"}
    with patch(HUB, return_value=hub):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], url)
    assert result["step_id"] == "lines"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_generic_duplicate(hass: HomeAssistant, hub: FakeHub) -> None:
    url = "https://example.test/gtfs.zip"
    MockConfigEntry(domain=DOMAIN, unique_id=f"generic_{url}").add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "generic"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": url}
    )
    assert result["reason"] == "already_configured"


async def test_abandoned_setup_deletes_its_database(hass: HomeAssistant, hub: FakeHub) -> None:
    path: Path = database_path(hass, "fake")
    await hass.async_add_executor_job(path.parent.mkdir, 0o755, True, True)
    await hass.async_add_executor_job(path.write_bytes, b"x")
    with patch(HUB, return_value=hub):
        result = await _to_lines(hass)
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    assert not await hass.async_add_executor_job(path.exists)


async def _setup(
    hass: HomeAssistant,
    *subentries: ConfigSubentryData,
    options: dict[str, list[str] | int] | None = None,
) -> MockConfigEntry:
    entry = make_entry(*subentries, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _options(
    hass: HomeAssistant, entry: MockConfigEntry, lines: dict[str, list[str]], stops: list[str]
) -> ConfigFlowResult:
    flow = hass.config_entries.options
    result = await flow.async_init(entry.entry_id)
    assert result["step_id"] == "lines"
    result = await flow.async_configure(result["flow_id"], lines)
    assert result["step_id"] == "stops"
    result = await flow.async_configure(result["flow_id"], {"stops": stops})
    assert result["step_id"] == "persons"
    return await flow.async_configure(result["flow_id"], {"persons": ["person.ania"]})


async def test_options_fill_in_current_choices(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"]
    assert schema is not None
    [field] = [key for key in schema.schema if str(key) == "bus"]
    assert field.description == {"suggested_value": ["3:11"]}
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_options_add_and_remove_with_one_reload(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry(), stop_subentry())
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        result = await _options(hass, entry, {"tram": ["0:11"]}, ["B"])
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    reload.assert_called_once_with(entry.entry_id)
    assert [item.unique_id for item in entry.get_subentries_of_type("line")] == ["0:11"]
    assert [item.unique_id for item in entry.get_subentries_of_type("stop")] == ["B"]
    assert entry.options == {"persons": ["person.ania"], "live_interval": 30}
    assert hass.states.get("sensor.fake_nearest_stop_ania") is not None


async def test_options_on_an_unloaded_entry(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["reason"] == "entry_not_loaded"


async def test_options_with_no_schedule(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    hub.ready = False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["reason"] == "download_failed"


async def test_same_persons_still_reload(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(
        hass, line_subentry(), options={"persons": ["person.ania"], "live_interval": 30}
    )
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        await _options(hass, entry, {"bus": ["3:11"]}, [])
    reload.assert_called_once_with(entry.entry_id)


async def test_options_keep_unchanged_choices(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry(), stop_subentry())
    ids = set(entry.subentries)
    await _options(hass, entry, {"bus": ["3:11"]}, ["A"])
    await hass.async_block_till_done()
    assert set(entry.subentries) == ids


async def test_live_interval_applies_after_saving(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    assert entry.runtime_data.departures.update_interval == timedelta(seconds=30)
    flow = hass.config_entries.options
    result = await flow.async_init(entry.entry_id)
    result = await flow.async_configure(result["flow_id"], {"bus": ["3:11"]})
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {"live_interval": 15})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["live_interval"] == 15
    assert entry.runtime_data.departures.update_interval == timedelta(seconds=15)


async def test_cancelled_options_keep_stored_lines(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    flow = hass.config_entries.options
    result = await flow.async_init(entry.entry_id)
    result = await flow.async_configure(result["flow_id"], {"tram": ["0:11"]})
    assert result["step_id"] == "stops"
    assert LineKey(3, "11") in hub.synced[-1]
    flow.async_abort(result["flow_id"])
