"""Whether a person is at a stop. Free of Home Assistant so it can be tested alone."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .nearest import distance

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_RADIUS: Final = 50
MIN_RADIUS: Final = 10
MAX_RADIUS: Final = 500
# A person leaves only this far beyond the radius, so GPS jitter at the edge fires nothing.
EXIT_MARGIN: Final = 50


@dataclass(frozen=True, slots=True)
class Reading:
    """Where a tracker places a person relative to a stop."""

    distance: float
    # 0 when the tracker gives none, as a router or a manual location is taken as exact.
    accuracy: float

    def within(self, radius: float) -> bool:
        """Return whether the reading puts the person within the radius for sure."""
        return self.accuracy <= radius and self.distance <= radius


def _number(value: object) -> float | None:
    """Return a real number, as attributes come from any tracker."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def measure(attributes: Mapping[str, object], latitude: float, longitude: float) -> Reading | None:
    """Return a reading against a point, or None when the tracker gives no coordinates."""
    person_latitude = _number(attributes.get("latitude"))
    person_longitude = _number(attributes.get("longitude"))
    if person_latitude is None or person_longitude is None:
        return None
    accuracy = _number(attributes.get("gps_accuracy"))
    return Reading(
        distance(person_latitude, person_longitude, latitude, longitude),
        0.0 if accuracy is None else max(accuracy, 0.0),
    )


def at_stop(was_at_stop: bool, reading: Reading | None, radius: float) -> bool:
    """Return whether the person is at the stop after a reading.

    A person comes in within the radius, with a fix at least as precise as the radius.
    They go out beyond the radius and the margin. A coarse fix takes them out only when
    even its near edge is that far, as phones in the background often report coarse fixes.
    Any other reading, and one without coordinates, changes nothing.
    """
    if reading is None:
        return was_at_stop
    if not was_at_stop:
        return reading.within(radius)
    if reading.accuracy > radius:
        return reading.distance - reading.accuracy <= radius + EXIT_MARGIN
    return reading.distance <= radius + EXIT_MARGIN
