"""Map marker images: the map cannot rotate a marker, so the image holds the arrow."""

from html import escape
from typing import Final
from urllib.parse import quote

from aiohttp import web
from homeassistant.helpers.http import HomeAssistantView

_COLOURS: Final = {0: "#C62828", 3: "#1565C0", 11: "#2E7D32"}
_DEFAULT_COLOUR: Final = "#455A64"
_STEP: Final = 15
_MAX_LINE: Final = 24


def marker_url(route_type: int, line: str, bearing: float | None) -> str:
    """Return the image of a vehicle, rounding the bearing so few images exist."""
    direction = "none" if bearing is None else str(round(bearing / _STEP) * _STEP % 360)
    return f"/api/gtfs_hub/marker/{route_type}/{direction}/{quote(line, safe='')}"


def marker_svg(route_type: int, line: str, bearing: int | None) -> str:
    """Draw a marker. The arrow sits inside the circle, as the map crops to one."""
    colour = _COLOURS.get(route_type, _DEFAULT_COLOUR)
    size = 40 if len(line) < 3 else 30 if len(line) < 5 else 20
    arrow = (
        ""
        if bearing is None
        else f'<path d="M50 2 L62 20 L38 20 Z" fill="#FFD600" transform="rotate({bearing} 50 50)"/>'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<circle cx="50" cy="50" r="50" fill="{colour}"/>{arrow}'
        f'<text x="50" y="50" dy=".35em" text-anchor="middle" font-family="sans-serif" '
        f'font-weight="bold" font-size="{size}" fill="#FFFFFF">{escape(line)}</text></svg>'
    )


class MarkerView(HomeAssistantView):
    """Serve marker images, which hold nothing private and load without a token."""

    url = "/api/gtfs_hub/marker/{route_type:\\d+}/{direction}/{line}"
    name = "api:gtfs_hub:marker"
    requires_auth = False

    async def get(
        self, request: web.Request, route_type: str, direction: str, line: str
    ) -> web.Response:
        """Return one marker."""
        if len(line) > _MAX_LINE or not (direction == "none" or direction.isdecimal()):
            raise web.HTTPNotFound
        bearing = None if direction == "none" else int(direction) % 360
        return web.Response(
            text=marker_svg(int(route_type), line, bearing),
            content_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )
