"""Map marker images: the map cannot rotate a marker, so the image holds the arrow."""

from html import escape
from typing import TYPE_CHECKING, Final
from urllib.parse import quote

from aiohttp import web
from homeassistant.helpers.http import HomeAssistantView

if TYPE_CHECKING:
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


def mode_of(route_type: int) -> str:
    """Return the mode of a line, for basic and extended route types."""
    if route_type == 3 or 700 <= route_type < 800:
        return "bus"
    if route_type == 0 or 900 <= route_type < 1000:
        return "tram"
    if route_type == 11 or 800 <= route_type < 900:
        return "trolleybus"
    return "other"


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


def marker_url(route_type: int, form: str, line: str, bearing: float | None) -> str:
    """Return the image of a vehicle, rounding the bearing so few images exist."""
    direction = "none" if bearing is None else str(round(bearing / _STEP) * _STEP % 360)
    return f"/api/gtfs_hub/marker/{route_type}/{form}/{direction}/{quote(line, safe='')}"


def _car(x: int, width: int, colour: str, wheels: tuple[int, ...]) -> str:
    """Draw one body from the side, with a window strip."""
    circles = "".join(
        f'<circle cx="{wheel}" cy="44" r="4.5" fill="#FFFFFF" stroke="{colour}" stroke-width="2"/>'
        for wheel in wheels
    )
    return (
        f'<rect x="{x}" y="24" width="{width}" height="19" rx="4" fill="#FFFFFF"/>'
        f'<rect x="{x + 3}" y="27" width="{width - 6}" height="6" rx="1.5" fill="{colour}"/>'
        f"{circles}"
    )


def _picture(mode: str, form: str, colour: str) -> str:
    """Draw a side view. Sizes differ by bus; trams differ by the number of units."""
    if form == "double":
        cars = _car(12, 37, colour, (20, 41)) + _car(51, 37, colour, (59, 80))
    elif form == "long":
        cars = (
            _car(15, 33, colour, (23, 41))
            + '<rect x="48" y="27" width="4" height="13" fill="#FFFFFF"/>'
            + _car(52, 33, colour, (59, 77))
        )
    elif form == "short":
        cars = _car(30, 40, colour, (39, 61))
    else:
        cars = _car(21, 58, colour, (33, 67))
    if mode == "tram":
        return (
            cars + '<path d="M50 24 L43 18 L52 13 M44 13 H60" fill="none" stroke="#FFFFFF" '
            'stroke-width="2.5"/>'
        )
    if mode == "trolleybus":
        return (
            cars + '<path d="M40 24 L60 12 M47 24 L67 12" fill="none" stroke="#FFFFFF" '
            'stroke-width="2.5"/>'
        )
    return cars


def marker_svg(route_type: int, form: str, line: str, bearing: int | None) -> str:
    """Draw a marker. Everything sits inside the circle, as the map crops to one."""
    mode = mode_of(route_type)
    colour = _COLOURS.get(mode, _DEFAULT_COLOUR)
    low_floor = form.endswith(_LOW_FLOOR)
    size = 32 if len(line) < 3 else 25 if len(line) < 5 else 17
    ring = '<circle cx="50" cy="50" r="45" fill="none" stroke="#FFFFFF" stroke-width="7"/>'
    arrow = (
        ""
        if bearing is None
        else f'<path d="M50 0 L61 13 L39 13 Z" fill="#FFD600" stroke="#000000" stroke-width="1" '
        f'transform="rotate({bearing} 50 50)"/>'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="50" fill="{colour}"/>'
        f"{ring if low_floor else ''}{_picture(mode, form.removesuffix(_LOW_FLOOR), colour)}{arrow}"
        f'<text x="50" y="72" dy=".35em" text-anchor="middle" font-family="sans-serif" '
        f'font-weight="bold" font-size="{size}" fill="#FFFFFF">{escape(line)}</text></svg>'
    )


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
        return web.Response(
            text=marker_svg(int(route_type), form, line, bearing),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
