"""Triggers for a person reaching or leaving a stop added in the configuration.

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
from .pinned import STOP_OPTIONS, TARGET_DOMAINS, PinnedStop, StopLookup
from .presence import at_stop, measure

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timedelta

    from homeassistant.helpers.typing import ConfigType

_SCHEMA = ENTITY_STATE_TRIGGER_SCHEMA_WITH_BEHAVIOR.extend(
    {vol.Required(CONF_OPTIONS): STOP_OPTIONS}
)


def target_entities(entities: set[str]) -> set[str]:
    """Keep the persons and device trackers of a target."""
    return {entity_id for entity_id in entities if split_entity_id(entity_id)[0] in TARGET_DOMAINS}


def presence_after(was_at_stop: bool, state: State | None, stop: PinnedStop, radius: float) -> bool:
    """Return whether a target is at the stop after a state."""
    reading = None if state is None else measure(state.attributes, stop.latitude, stop.longitude)
    return at_stop(was_at_stop, reading, radius)


class _StopPresenceTrigger(Trigger):
    """Fires when a target's presence at a stop turns to the wanted value."""

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
        self._stop = StopLookup(
            hass, config.options[CONF_STOP], f"the {self._verb} trigger never fires"
        )
        self._radius: float = config.options[CONF_RADIUS]
        self._behavior: str = config.options[ATTR_BEHAVIOR]
        self._duration: timedelta | None = config.options.get(CONF_FOR)

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        did_not_trigger: TriggerNotTriggeredReporter | None = None,
    ) -> CALLBACK_TYPE:
        """Attach the trigger to an action runner."""
        presence: dict[str, bool] = {}
        # Pending `for` timers, by entity for `each` and by the behavior otherwise.
        pending: dict[str, CALLBACK_TYPE] = {}

        def known(entity_id: str, state: State | None, stop: PinnedStop) -> bool:
            """Return a target's presence, first judged from its state alone."""
            if entity_id not in presence:
                presence[entity_id] = presence_after(False, state, stop, self._radius)
            return presence[entity_id]

        def count(states: Mapping[str, State | None], stop: PinnedStop) -> tuple[int, int]:
            """Return how many targets have the wanted presence, and how many there are."""
            values = [known(entity_id, state, stop) for entity_id, state in states.items()]
            return sum(value is self._wanted for value in values), len(values)

        def combined_holds(states: Mapping[str, State | None], stop: PinnedStop) -> bool:
            hits, total = count(states, stop)
            if self._behavior == BEHAVIOR_FIRST:
                return hits >= 1
            return total > 0 and hits == total

        def forget() -> None:
            presence.clear()
            for cancel in pending.values():
                cancel()
            pending.clear()

        @callback
        def entities_updated(
            added: set[str], removed: set[str], states: Mapping[str, State | None]
        ) -> None:
            """Forget targets that left, and recheck a pending combined match."""
            for entity_id in removed:
                presence.pop(entity_id, None)
                if (cancel := pending.pop(entity_id, None)) is not None:
                    cancel()
            if self._behavior not in pending:
                return
            stop = self._stop()
            if stop is None or not combined_holds(states, stop):
                pending.pop(self._behavior)()

        @callback
        def state_changed(data: TargetStateChangedData) -> None:
            event = data.state_change_event
            entity_id = event.data["entity_id"]
            from_state = event.data["old_state"]
            to_state = event.data["new_state"]
            stop = self._stop()
            if stop is None:
                # Moves made now go unseen, so where targets were is learnt again afterwards.
                forget()
                return
            # The first change seen only tells where the target was, so a restart fires nothing.
            was = known(entity_id, from_state, stop)
            now = presence_after(was, to_state, stop, self._radius)
            presence[entity_id] = now
            states = {
                key: value
                for key, value in data.targeted_entity_states.items()
                if key in data.targeted_entity_ids
            }
            key = entity_id if self._behavior == BEHAVIOR_EACH else self._behavior
            if key in pending:
                holds = now is self._wanted
                if self._behavior != BEHAVIOR_EACH:
                    holds = combined_holds(states, stop)
                if not holds:
                    pending.pop(key)()
            if was is now or now is not self._wanted or to_state is None:
                return
            if self._behavior == BEHAVIOR_ALL and not combined_holds(states, stop):
                return
            if self._behavior == BEHAVIOR_FIRST and count(states, stop)[0] != 1:
                return
            reading = measure(to_state.attributes, stop.latitude, stop.longitude)

            @callback
            def fire() -> None:
                current = self._stop()
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
            if (previous := pending.pop(key, None)) is not None:
                previous()

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
