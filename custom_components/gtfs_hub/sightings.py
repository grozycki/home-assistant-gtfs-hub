"""When each vehicle tracker last saw its vehicle, kept across restarts.

Time counts only while the feed shows vehicles of tracked lines. Home Assistant being off, a
feed outage or a night without service says nothing about one vehicle, so none of it counts.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, TypedDict

from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

# A vehicle off service for a weekend keeps its tracker.
RETENTION: Final = timedelta(days=3)
SWEEP_INTERVAL: Final = timedelta(minutes=10)
_VERSION: Final = 1


class _Stored(TypedDict):
    clock: float
    seen: dict[str, float]


def _store(hass: HomeAssistant, entry_id: str) -> Store[_Stored]:
    return Store(hass, _VERSION, f"{DOMAIN}.{entry_id}.sightings")


async def async_remove_sightings(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the sightings of a removed entry."""
    await _store(hass, entry_id).async_remove()


class Sightings:
    """Last sightings by tracker unique ID, on a clock that runs only while vehicles are seen."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = _store(hass, entry_id)
        # Seconds of observed service, carried over restarts.
        self._clock = 0.0
        self._seen: dict[str, float] = {}
        self._last_tick: datetime | None = None
        self._swept: datetime | None = None

    async def async_load(self, trackers: Iterable[str]) -> None:
        """Read stored sightings, counting a tracker with none as seen now."""
        stored: object = await self._store.async_load()
        seen: dict[str, float] = {}
        # A file of another shape is read as none, which only delays removals.
        if isinstance(stored, dict) and isinstance(clock := stored.get("clock"), int | float):
            self._clock = float(clock)
            if isinstance(found := stored.get("seen"), dict):
                seen = {
                    str(key): float(value)
                    for key, value in found.items()
                    if isinstance(value, int | float)
                }
        for unique_id in trackers:
            self._seen[unique_id] = seen.get(unique_id, self._clock)

    def tick(self, now: datetime, live: bool) -> None:
        """Advance the clock by the time since the last tick, when vehicles are in the feed."""
        if live and self._last_tick is not None:
            self._clock += max((now - self._last_tick).total_seconds(), 0.0)
        self._last_tick = now

    def saw(self, unique_id: str) -> None:
        """Record that a tracker's vehicle is in the feed."""
        self._seen[unique_id] = self._clock

    def sweep(self, now: datetime) -> list[str]:
        """Return trackers unseen for too long and forget them, once a sweep interval."""
        if self._swept is not None and now - self._swept < SWEEP_INTERVAL:
            return []
        self._swept = now
        limit = RETENTION.total_seconds()
        gone = [key for key, seen in self._seen.items() if self._clock - seen >= limit]
        for unique_id in gone:
            del self._seen[unique_id]
        # Losing up to one sweep interval of sightings only delays a removal.
        self._store.async_delay_save(self._data, 1)
        return gone

    def _data(self) -> _Stored:
        return {"clock": self._clock, "seen": dict(self._seen)}
