"""Find the stop a person is near. Free of Home Assistant so it can be tested alone."""

import math
from collections.abc import Iterable

from gtfs_hub import Stop

MAX_DISTANCE: float = 2_000
SWITCH_MARGIN: float = 50


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half_lat = (phi2 - phi1) / 2
    half_lon = math.radians(lon2 - lon1) / 2
    term = math.sin(half_lat) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_lon) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(term))


def find_nearest(
    latitude: float, longitude: float, stops: Iterable[Stop], current: str | None
) -> tuple[Stop, float] | None:
    """Return the nearest stop and its distance, keeping the current one unless clearly beaten."""
    measured = [
        (distance(latitude, longitude, stop.latitude, stop.longitude), stop)
        for stop in stops
        if stop.latitude is not None and stop.longitude is not None
    ]
    if not measured:
        return None
    best_distance, best = min(measured, key=lambda item: (item[0], item[1].key))
    if best_distance > MAX_DISTANCE:
        return None
    for held_distance, stop in measured:
        if stop.key == current and held_distance - best_distance < SWITCH_MARGIN:
            return (stop, held_distance) if held_distance <= MAX_DISTANCE else (best, best_distance)
    return best, best_distance
