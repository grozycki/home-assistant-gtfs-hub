from unittest.mock import patch

from custom_components.gtfs_hub.const import DOMAIN
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from conftest import FakeHub, FakeProvider, line_subentry, make_entry
from gtfs_hub import GtfsConnectionError, GtfsError, GtfsFeedError

BUILD = "custom_components.gtfs_hub.config_flow.async_build_provider"


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_installed_provider(hass: HomeAssistant, hub: FakeHub) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"provider": "fake"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fake"
    assert result["data"] == {"provider": "fake"}


async def test_errors_then_recovery(hass: HomeAssistant, hub: FakeHub) -> None:
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
    with patch(BUILD, return_value=FakeProvider()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"provider": "fake"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_provider(hass: HomeAssistant, hub: FakeHub) -> None:
    make_entry().add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"provider": "fake"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_generic_provider(hass: HomeAssistant, hub: FakeHub) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"provider": "generic"}
    )
    assert result["step_id"] == "generic"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"schedule_url": "https://example.test/gtfs.zip"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["provider"] == "generic"
    assert result["result"].unique_id == "generic_https://example.test/gtfs.zip"


async def _setup(hass: HomeAssistant, *subentries, options=None):
    entry = make_entry(*subentries, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_add_line_offers_both_modes(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass)
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "line"), context={"source": "user"})
    assert result["step_id"] == "user"
    result = await flows.async_configure(result["flow_id"], {"query": "none"})
    assert result["errors"] == {"base": "no_matches"}
    result = await flows.async_configure(result["flow_id"], {"query": "11"})
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema["line"].config["options"]
    assert [option["label"] for option in options] == ["Bus 11: Long", "Tram 11"]
    result = await flows.async_configure(result["flow_id"], {"line": "0:11"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    [subentry] = entry.get_subentries_of_type("line")
    assert subentry.data == {"route_type": 0, "short_name": "11"}


async def test_add_line_twice_and_not_ready(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "line"), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"query": "11"})
    result = await flows.async_configure(result["flow_id"], {"line": "3:11"})
    assert result["reason"] == "already_configured"
    hub.ready = False
    result = await flows.async_init((entry.entry_id, "line"), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"query": "11"})
    assert result["reason"] == "catalogue_not_ready"


async def test_add_stop_only_from_selected_lines(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "stop"), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"query": "none"})
    assert result["errors"] == {"base": "no_matches"}
    result = await flows.async_configure(result["flow_id"], {"query": "a"})
    options = result["data_schema"].schema["stop"].config["options"]
    assert options == [{"value": "A", "label": "Alfa (C-A)"}]
    result = await flows.async_configure(result["flow_id"], {"stop": "A"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.alfa_c_a_next_departure") is not None


async def test_stop_needs_a_line(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "stop"), context={"source": "user"}
    )
    assert result["reason"] == "no_lines"


async def test_subentry_needs_a_loaded_entry(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    for kind in ("line", "stop"):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, kind), context={"source": "user"}
        )
        assert result["reason"] == "entry_not_loaded"


async def test_options_choose_persons(hass: HomeAssistant, hub: FakeHub) -> None:
    entry = await _setup(hass, line_subentry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"persons": ["person.ania"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("sensor.fake_nearest_stop_ania") is not None
