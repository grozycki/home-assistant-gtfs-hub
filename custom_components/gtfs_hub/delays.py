"""Rules that turn trip delays into events, in plain Python so they can be tested alone."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from gtfs_hub import TripDelay, TripId

# A value must hold this many ticks, as one update can be an outlier.
HOLD: Final = 2
# Recovery needs the delay this far below the threshold, so a delay hovering there stays quiet.
HYSTERESIS: Final = 120
FORGET_AFTER: Final = timedelta(hours=1)


class DelayEventType(StrEnum):
    """What happened to a trip."""

    DELAYED = "delayed"
    RECOVERED = "recovered"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DelayEvent:
    """One event and the trip it is about."""

    type: DelayEventType
    trip: TripDelay


@dataclass(slots=True)
class _TripState:
    seen: datetime
    delayed: bool = False
    cancelled: bool = False
    # Ticks in a row that point to the other state.
    streak: int = 0


class DelayRules:
    """Fire each event once per trip, from one tick of trip delays after another."""

    def __init__(self, threshold: int) -> None:
        """Take the threshold in seconds."""
        self._threshold = threshold
        self._recover_at = max(threshold - HYSTERESIS, 0)
        self._trips: dict[tuple[TripId, date | None], _TripState] = {}
        # The first tick only records the state, or every running delay would fire at startup.
        self._started = False

    def update(self, trips: list[TripDelay] | None, now: datetime) -> list[DelayEvent]:
        """Apply one tick. None means no trustworthy live data, which pauses the rules."""
        if trips is None:
            # A gap breaks every streak, as a value must hold on ticks in a row. Trips seen
            # before it keep their state, so they neither fire again nor are lost.
            for known in self._trips.values():
                known.streak = 0
            return []
        silent = not self._started
        self._started = True
        events: list[DelayEvent] = []
        for trip in trips:
            key = (trip.trip_id, trip.start_date)
            state = self._trips.get(key)
            if state is None:
                state = self._trips[key] = _TripState(now)
            state.seen = now
            event = self._apply(state, trip, silent)
            if event is not None:
                events.append(DelayEvent(event, trip))
        # A trip that left the feed fires nothing: unknown is not recovered.
        for key, state in list(self._trips.items()):
            if now - state.seen > FORGET_AFTER:
                del self._trips[key]
        return events

    def _apply(self, state: _TripState, trip: TripDelay, silent: bool) -> DelayEventType | None:
        if trip.cancelled:
            if state.cancelled:
                return None
            state.cancelled = True
            return None if silent else DelayEventType.CANCELLED
        # A feed may withdraw a cancellation, and the trip then counts like any other.
        state.cancelled = False
        delay = trip.delay
        if delay is None:
            state.streak = 0
            return None
        if silent:
            state.delayed = delay >= self._threshold
            return None
        leaving = delay <= self._recover_at if state.delayed else delay >= self._threshold
        state.streak = state.streak + 1 if leaving else 0
        if state.streak < HOLD:
            return None
        state.streak = 0
        state.delayed = not state.delayed
        return DelayEventType.DELAYED if state.delayed else DelayEventType.RECOVERED
