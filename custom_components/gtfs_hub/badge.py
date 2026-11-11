"""Badge colours of a line, readable whatever colours the source gives it."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .marker import contrast, mode_colour

if TYPE_CHECKING:
    from gtfs_hub import Line, Livery

_WHITE: Final = "#FFFFFF"
# WCAG's floor for large text, which a line number on a badge is.
MIN_CONTRAST: Final = 3.0
_HEX: Final = re.compile(r"#?([0-9A-Fa-f]{6})")


@dataclass(frozen=True, slots=True)
class LineBadge:
    """How to show a line: text on a background, each as #RRGGBB."""

    background: str
    text: str


def as_colour(value: str | None) -> str | None:
    """Return a colour as #RRGGBB, or None when it is not one. Some feeds add the hash."""
    found = _HEX.fullmatch(value.strip()) if value else None
    return f"#{found[1].upper()}" if found else None


def line_badge(line: Line | None, route_type: int, livery: Livery | None) -> LineBadge:
    """Return the badge of a line: the fleet's livery, readable source colours, or the mode's."""
    if livery is not None:
        return LineBadge(livery.trim, livery.body)
    background = as_colour(line.color) if line else None
    text = as_colour(line.text_color) if line else None
    if background is not None and text is not None and contrast(background, text) >= MIN_CONTRAST:
        return LineBadge(background, text)
    return LineBadge(mode_colour(route_type), _WHITE)
