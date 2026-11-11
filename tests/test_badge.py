"""Badge colours: livery first, then readable source colours, then the mode."""

from typing import TYPE_CHECKING

import pytest
from custom_components.gtfs_hub.badge import LineBadge, as_colour, line_badge
from custom_components.gtfs_hub.marker import contrast

from conftest import (
    LINE_11,
    FakeHub,
    departure,
    get_state,
    line_subentry,
    make_entry,
    stop_subentry,
)
from gtfs_hub import Line, Livery

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

GZM = Livery(body="#FFD200", trim="#1A1A1A")


def line(color: str | None, text: str | None) -> Line:
    return Line(LINE_11, None, None, color, text)


def test_livery_wins() -> None:
    assert line_badge(line("FF0000", "FFFFFF"), 3, GZM) == LineBadge("#1A1A1A", "#FFD200")


def test_readable_source_colours() -> None:
    assert line_badge(line("ff0000", "#ffffff"), 3, None) == LineBadge("#FF0000", "#FFFFFF")


@pytest.mark.parametrize(
    ("color", "text"),
    [("000000", "000000"), ("000000", "202020"), (None, "FFFFFF"), ("red", "FFF")],
)
def test_unreadable_or_missing_colours_fall_back_to_the_mode(
    color: str | None, text: str | None
) -> None:
    assert line_badge(line(color, text), 0, None) == LineBadge("#C62828", "#FFFFFF")


def test_no_line_falls_back_to_the_mode() -> None:
    assert line_badge(None, 3, None) == LineBadge("#1565C0", "#FFFFFF")


def test_contrast_of_black_and_white() -> None:
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00ffff", "#00FFFF"),
        ("#00FFFF", "#00FFFF"),
        (" 00FFFF ", "#00FFFF"),
        ("", None),
        (None, None),
        ("0FF", None),
    ],
)
def test_colours_are_normalised(value: str | None, expected: str | None) -> None:
    assert as_colour(value) == expected


async def test_departures_carry_the_badge(hass: HomeAssistant, hub: FakeHub) -> None:
    hub.provider.livery = GZM
    hub.departures[departure().stop_key] = [departure()]
    entry = make_entry(line_subentry(), stop_subentry())
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    [item] = get_state(hass, "sensor.alfa_next_departure").attributes["departures"]
    assert (item["line_color"], item["line_text_color"]) == ("#1A1A1A", "#FFD200")
