"""Map marker images: the map cannot rotate a marker, so the image holds the arrow."""

import re
from html import escape
from typing import TYPE_CHECKING, Final
from urllib.parse import quote, urlencode

from aiohttp import web
from homeassistant.helpers.http import HomeAssistantView

from gtfs_hub import Livery

if TYPE_CHECKING:
    from collections.abc import Mapping

    from gtfs_hub import VehicleClass

STOP_MARKER_URL: Final = "/api/gtfs_hub/stop_marker"
_COLOURS: Final = {"bus": "#1565C0", "tram": "#C62828", "trolleybus": "#2E7D32"}
_DEFAULT_COLOUR: Final = "#455A64"
_FORMS: Final = ("short", "standard", "long", "double")
_LOW_FLOOR: Final = "-low"
_STEP: Final = 15
_MAX_LINE: Final = 24
_SHORT_BELOW: Final = 10
_LONG_FROM: Final = 15
_WHITE: Final = "#FFFFFF"
_BLACK: Final = "#000000"
_ARROW: Final = "#FFD600"
# A livery picks the arrow that stands out most against both of its colours.
_ARROW_CHOICES: Final = (_ARROW, "#E53935", _WHITE, _BLACK)
# Colours land in SVG, so only plain hex passes.
_HEX: Final = re.compile(r"[0-9A-Fa-f]{6}")


def mode_of(route_type: int) -> str:
    """Return the mode of a line, for basic and extended route types."""
    if route_type == 3 or 700 <= route_type < 800:
        return "bus"
    if route_type == 0 or 900 <= route_type < 1000:
        return "tram"
    if route_type == 11 or 800 <= route_type < 900:
        return "trolleybus"
    return "other"


def mode_colour(route_type: int) -> str:
    """Return the colour of a mode, which markers and badges share."""
    return _COLOURS.get(mode_of(route_type), _DEFAULT_COLOUR)


def _luminance(colour: str) -> float:
    channels = [int(colour[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    """Return the WCAG contrast ratio of two #RRGGBB colours."""
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def arrow_colours(livery: Livery | None) -> tuple[str, str]:
    """Return the fill and edge of the direction arrow."""
    if livery is None:
        return _ARROW, _BLACK
    fill = max(
        _ARROW_CHOICES,
        key=lambda colour: min(contrast(colour, livery.body), contrast(colour, livery.trim)),
    )
    # The edge is whichever of white and black stands out more against the fill.
    return fill, _WHITE if contrast(fill, _WHITE) > contrast(fill, _BLACK) else _BLACK


def form_of(planned: VehicleClass | None) -> str:
    """Return the picture of a vehicle, with a mark for a low floor."""
    if planned is None:
        return "standard"
    form = "standard"
    if planned.units > 1:
        form = "double"
    elif planned.length is not None and planned.length < _SHORT_BELOW:
        form = "short"
    elif planned.length is not None and planned.length >= _LONG_FROM:
        form = "long"
    return f"{form}{_LOW_FLOOR}" if planned.low_floor else form


def marker_url(
    route_type: int, form: str, line: str, bearing: float | None, livery: Livery | None = None
) -> str:
    """Return the image of a vehicle, rounding the bearing so few images exist."""
    direction = "none" if bearing is None else str(round(bearing / _STEP) * _STEP % 360)
    url = f"/api/gtfs_hub/marker/{route_type}/{form}/{direction}/{quote(line, safe='')}"
    if livery is None:
        return url
    query = {"body": livery.body.removeprefix("#"), "trim": livery.trim.removeprefix("#")}
    return f"{url}?{urlencode(query)}"


def _car(x: int, width: int, body: str, trim: str, wheels: tuple[int, ...]) -> str:
    """Draw one body from the side, with a window strip."""
    circles = "".join(
        f'<circle cx="{wheel}" cy="44" r="4.5" fill="{body}" stroke="{trim}" stroke-width="2"/>'
        for wheel in wheels
    )
    return (
        f'<rect x="{x}" y="24" width="{width}" height="19" rx="4" fill="{body}"/>'
        f'<rect x="{x + 3}" y="27" width="{width - 6}" height="6" rx="1.5" fill="{trim}"/>'
        f"{circles}"
    )


def _picture(mode: str, form: str, body: str, trim: str) -> str:
    """Draw a side view. Sizes differ by bus; trams differ by the number of units."""
    if form == "double":
        cars = _car(12, 37, body, trim, (20, 41)) + _car(51, 37, body, trim, (59, 80))
    elif form == "long":
        cars = (
            _car(15, 33, body, trim, (23, 41))
            + f'<rect x="48" y="27" width="4" height="13" fill="{body}"/>'
            + _car(52, 33, body, trim, (59, 77))
        )
    elif form == "short":
        cars = _car(30, 40, body, trim, (39, 61))
    else:
        cars = _car(21, 58, body, trim, (33, 67))
    if mode == "tram":
        return (
            cars + f'<path d="M50 24 L43 18 L52 13 M44 13 H60" fill="none" stroke="{body}" '
            'stroke-width="2.5"/>'
        )
    if mode == "trolleybus":
        return (
            cars + f'<path d="M40 24 L60 12 M47 24 L67 12" fill="none" stroke="{body}" '
            'stroke-width="2.5"/>'
        )
    return cars


def marker_svg(
    route_type: int, form: str, line: str, bearing: int | None, livery: Livery | None = None
) -> str:
    """Draw a marker. Everything sits inside the circle, as the map crops to one.

    A livery paints the vehicle as the fleet looks; without one, the colour tells the mode.
    """
    mode = mode_of(route_type)
    if livery is None:
        background = mode_colour(route_type)
        body, trim = _WHITE, background
    else:
        background = trim = livery.trim
        body = livery.body
    low_floor = form.endswith(_LOW_FLOOR)
    size = 32 if len(line) < 3 else 25 if len(line) < 5 else 17
    ring = f'<circle cx="50" cy="50" r="45" fill="none" stroke="{body}" stroke-width="7"/>'
    fill, edge = arrow_colours(livery)
    width = "1" if livery is None else "1.5"
    arrow = (
        ""
        if bearing is None
        else f'<path d="M50 0 L61 13 L39 13 Z" fill="{fill}" stroke="{edge}" '
        f'stroke-width="{width}" transform="rotate({bearing} 50 50)"/>'
    )
    picture = _picture(mode, form.removesuffix(_LOW_FLOOR), body, trim)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="50" fill="{background}"/>'
        f"{ring if low_floor else ''}{picture}{arrow}"
        f'<text x="50" y="72" dy=".35em" text-anchor="middle" font-family="sans-serif" '
        f'font-weight="bold" font-size="{size}" fill="{body}">{escape(line)}</text></svg>'
    )


def _livery_of(query: Mapping[str, str]) -> Livery | None:
    """Read colours from a marker URL, refusing anything but plain hex."""
    body, trim = query.get("body"), query.get("trim")
    if body is None and trim is None:
        return None
    if body is None or trim is None or not (_HEX.fullmatch(body) and _HEX.fullmatch(trim)):
        raise web.HTTPNotFound
    return Livery(body=f"#{body}", trim=f"#{trim}")


def stop_svg() -> str:
    """Draw a stop sign, which tells stops from vehicles at a glance."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<circle cx="50" cy="50" r="50" fill="#263238"/>'
        '<rect x="47" y="44" width="6" height="42" rx="2" fill="#FFFFFF"/>'
        '<rect x="24" y="12" width="52" height="42" rx="6" fill="#FFD600"/>'
        '<rect x="32" y="20" width="36" height="20" rx="4" fill="#263238"/>'
        '<rect x="36" y="24" width="28" height="7" rx="1.5" fill="#FFD600"/>'
        '<circle cx="39" cy="43" r="4.5" fill="#263238"/>'
        '<circle cx="61" cy="43" r="4.5" fill="#263238"/>'
        "</svg>"
    )


class StopMarkerView(HomeAssistantView):
    """Serve the stop marker, which holds nothing private and loads without a token."""

    url = STOP_MARKER_URL
    name = "api:gtfs_hub:stop_marker"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        """Return the marker."""
        return web.Response(
            text=stop_svg(),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )


class MarkerView(HomeAssistantView):
    """Serve marker images, which hold nothing private and load without a token."""

    url = "/api/gtfs_hub/marker/{route_type:\\d+}/{form}/{direction}/{line}"
    name = "api:gtfs_hub:marker"
    requires_auth = False

    async def get(
        self, request: web.Request, route_type: str, form: str, direction: str, line: str
    ) -> web.Response:
        """Return one marker."""
        known = form.removesuffix(_LOW_FLOOR) in _FORMS
        if not known or len(line) > _MAX_LINE or not (direction == "none" or direction.isdecimal()):
            raise web.HTTPNotFound
        bearing = None if direction == "none" else int(direction) % 360
        livery = _livery_of(request.query)
        return web.Response(
            text=marker_svg(int(route_type), form, line, bearing, livery),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
