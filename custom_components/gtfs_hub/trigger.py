"""Triggers for a person reaching or leaving stops added in the configuration.

They behave like zone triggers, but whether a person is at the stop needs the last
reading, so they track state changes themselves rather than judge each state alone.
"""

from typing import TYPE_CHECKING, ClassVar, cast, override

import probatio as vol
from homeassistant.const import ATTR_ENTITY_ID, CONF_FOR, CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback, split_entity_id
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.target import (
    TargetStateChangedData,
    async_track_target_selector_state_change_event,
)
from homeassistant.helpers.trigger import (
    ATTR_BEHAVIOR,
    BEHAVIOR_ALL,
    BEHAVIOR_EACH,
    BEHAVIOR_FIRST,
    ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR,
    Trigger,
    TriggerActionRunner,
    TriggerConfig,
    TriggerNotTriggeredReporter,
)

from .const import CONF_RADIUS, CONF_STOP
from .pinned import STOP_OPTIONS, TARGET_DOMAINS, PinnedStop, StopsLookup
from .presence import current_stop, measure

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timedelta

    from homeassistant.helpers.typing import ConfigType

_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR.extend(
    {vol.Required(CONF_OPTIONS, default={}): STOP_OPTIONS}
)


def target_entities(entities: set[str]) -> set[str]:
    """Keep the persons and device trackers of a target."""
    return {entity_id for entity_id in entities if split_entity_id(entity_id)[0] in TARGET_DOMAINS}


def stop_after(
    was: str | None, state: State | None, stops: Mapping[str, PinnedStop], radius: float
) -> str | None:
    """Return the ID of the stop a target is at after a state, if any."""
    readings = {
        stop_id: None if state is None else measure(state.attributes, stop.latitude, stop.longitude)
        for stop_id, stop in stops.items()
    }
    return current_stop(was, readings, radius)


class _StopPresenceTrigger(Trigger):
    """Fires when a target comes to (or goes from) one of the stops it covers."""

    # True for entering, False for leaving.
    _wanted: ClassVar[bool]
    _verb: ClassVar[str]

    @override
    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        """Validate config."""
        return cast("ConfigType", _SCHEMA(config))

    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        super().__init__(hass, config)
        if TYPE_CHECKING:
            assert config.target is not None
            assert config.options is not None
        self._target = config.target
        self._stops = StopsLookup(
            hass, config.options[CONF_STOP], f"the {self._verb} trigger leaves it out"
        )
        self._radius: float = config.options[CONF_RADIUS]
        self._behavior: str = config.options[ATTR_BEHAVIOR]
        self._duration: timedelta | None = config.options.get(CONF_FOR)

    def _covered(self) -> dict[str, PinnedStop]:
        return {stop.id: stop for stop in self._stops()}

    def _holds(self, current: str | None, stop_id: str) -> bool:
        """Return whether a target's current stop is what this trigger waits for."""
        return (current == stop_id) is self._wanted

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: TriggerNotTriggeredReporter | None = None,
    ) -> CALLBACK_TYPE:
        """Attach the trigger to an action runner."""
        # The stop each target is at, by the stop's ID, or None at no stop.
        presence: dict[str, str | None] = {}
        # Pending `for` timers, by entity and stop for `each`, by behavior and stop otherwise.
        pending: dict[tuple[str, str], CALLBACK_TYPE] = {}

        def known(
            entity_id: str, state: State | None, stops: Mapping[str, PinnedStop]
        ) -> str | None:
            """Return a target's stop, judged from its state alone when it is new.

            A target whose stop cannot be found any more is judged again, as its moves
            went unseen meanwhile.
            """
            current = presence.get(entity_id)
            if entity_id not in presence or (current is not None and current not in stops):
                presence[entity_id] = stop_after(None, state, stops, self._radius)
            return presence[entity_id]

        def count(
            states: Mapping[str, State | None], stops: Mapping[str, PinnedStop], stop_id: str
        ) -> tuple[int, int]:
            """Return how many targets this trigger waits for at a stop, and how many there are."""
            values = [known(entity_id, state, stops) for entity_id, state in states.items()]
            return sum(self._holds(value, stop_id) for value in values), len(values)

        def combined_holds(
            states: Mapping[str, State | None], stops: Mapping[str, PinnedStop], stop_id: str
        ) -> bool:
            hits, total = count(states, stops, stop_id)
            if self._behavior == BEHAVIOR_FIRST:
                return hits >= 1
            return total > 0 and hits == total

        def cancel(key: tuple[str, str]) -> None:
            pending.pop(key)()

        def forget() -> None:
            presence.clear()
            for key in list(pending):
                cancel(key)

        def recheck(
            states: Mapping[str, State | None], stops: Mapping[str, PinnedStop], entity_id: str
        ) -> None:
            """Cancel `for` timers whose wait no longer holds."""
            for key in list(pending):
                owner, stop_id = key
                if stop_id not in stops:
                    cancel(key)
                elif self._behavior == BEHAVIOR_EACH:
                    if owner == entity_id and not self._holds(presence.get(owner), stop_id):
                        cancel(key)
                elif not combined_holds(states, stops, stop_id):
                    cancel(key)

        @callback
        def entities_updated(
            added: set[str], removed: set[str], states: Mapping[str, State | None]
        ) -> None:
            """Forget targets that left, and recheck pending combined matches."""
            for entity_id in removed:
                presence.pop(entity_id, None)
                for key in [key for key in pending if key[0] == entity_id]:
                    cancel(key)
            if self._behavior != BEHAVIOR_EACH and pending:
                recheck(states, self._covered(), "")

        @callback
        def state_changed(data: TargetStateChangedData) -> None:
            event = data.state_change_event
            entity_id = event.data["entity_id"]
            from_state = event.data["old_state"]
            to_state = event.data["new_state"]
            stops = self._covered()
            if not stops:
                # Moves made now go unseen, so where targets were is learnt again afterwards.
                forget()
                return
            # The first change seen only tells where the target was, so a restart fires nothing.
            # A target that only now appears, with no old state, is judged from its new one.
            was = known(entity_id, from_state or to_state, stops)
            now = stop_after(was, to_state, stops, self._radius)
            presence[entity_id] = now
            states = {
                key: value
                for key, value in data.targeted_entity_states.items()
                if key in data.targeted_entity_ids
            }
            recheck(states, stops, entity_id)
            changed = now if self._wanted else was
            if was == now or changed is None or to_state is None:
                return
            if self._behavior == BEHAVIOR_ALL and not combined_holds(states, stops, changed):
                return
            if self._behavior == BEHAVIOR_FIRST and count(states, stops, changed)[0] != 1:
                return
            stop = stops[changed]
            reading = measure(to_state.attributes, stop.latitude, stop.longitude)

            @callback
            def fire() -> None:
                current = self._covered().get(changed)
                if current is None:
                    return
                # The automation runs on its own; nothing here waits for it.
                _ = run_action(
                    {
                        ATTR_ENTITY_ID: entity_id,
                        "from_state": from_state,
                        "to_state": to_state,
                        "for": self._duration,
                        "stop": current.describe(),
                        "distance": None if reading is None else round(reading.distance),
                        "departures": current.departures(),
                    },
                    f"{entity_id} {self._verb} {current.stop.name}",
                    event.context,
                )

            if not self._duration:
                fire()
                return
            key = (entity_id if self._behavior == BEHAVIOR_EACH else self._behavior, changed)
            if key in pending:
                cancel(key)

            @callback
            def fire_after_duration(_now: datetime) -> None:
                del pending[key]
                fire()

            pending[key] = async_call_later(self._hass, self._duration, fire_after_duration)

        unsubscribe = await async_track_target_selector_state_change_event(
            self._hass, self._target, state_changed, target_entities, entities_updated
        )

        @callback
        def remove() -> None:
            unsubscribe()
            forget()

        return remove


class PersonEnteredStopTrigger(_StopPresenceTrigger):
    """A person comes to a stop."""

    _wanted = True
    _verb = "entered"


class PersonLeftStopTrigger(_StopPresenceTrigger):
    """A person goes away from a stop."""

    _wanted = False
    _verb = "left"


TRIGGERS: dict[str, type[Trigger]] = {
    "person_entered_stop": PersonEnteredStopTrigger,
    "person_left_stop": PersonLeftStopTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers of the integration."""
    return TRIGGERS
