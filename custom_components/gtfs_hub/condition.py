"""Condition for a person being at a stop added in the configuration."""

from typing import TYPE_CHECKING, cast, override

import probatio as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.helpers.automation import DomainSpec
from homeassistant.helpers.condition import (
    ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL,
    Condition,
    ConditionConfig,
    EntityConditionBase,
)

from .const import CONF_RADIUS, CONF_STOP
from .pinned import STOP_OPTIONS, TARGET_DOMAINS, StopsLookup
from .presence import measure

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant, State
    from homeassistant.helpers.typing import ConfigType

_DOMAIN_SPECS: Mapping[str, DomainSpec] = {domain: DomainSpec() for domain in TARGET_DOMAINS}
_SCHEMA = ENTITY_STATE_CONDITION_SCHEMA_ANY_ALL.extend(
    {vol.Required(CONF_OPTIONS, default={}): STOP_OPTIONS}
)


class PersonAtStopCondition(EntityConditionBase):
    """Passes when targets are within the radius of one of the stops now."""

    _domain_specs = _DOMAIN_SPECS
    _schema = _SCHEMA

    @override
    @classmethod
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        """Validate config."""
        return cast("ConfigType", _SCHEMA(config))

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        super().__init__(hass, config)
        if TYPE_CHECKING:
            assert config.options is not None
        self._stops = StopsLookup(
            hass, config.options[CONF_STOP], "the at stop condition leaves it out"
        )
        self._radius: float = config.options[CONF_RADIUS]

    @override
    def _should_include(self, _state: State) -> bool:
        """Count every target, as one in an unknown state is simply not at the stop.

        The base leaves unknown and unavailable targets out, so `all` would pass for none.
        """
        return True

    @override
    def _check_all_match_state(self, states: list[State]) -> bool:
        """Fail `all` when no target exists, rather than pass for none."""
        return bool(states) and super()._check_all_match_state(states)

    @override
    def _state_valid_since(self, _state: State) -> datetime:
        """Anchor `for` on the update, as a move changes attributes, not the state."""
        return _state.last_updated

    @override
    def is_valid_state(self, entity_state: State) -> bool:
        """Check that a target is at one of the stops."""
        # Only the radius counts: a condition sees no earlier reading to hold someone in.
        for stop in self._stops():
            reading = measure(entity_state.attributes, stop.latitude, stop.longitude)
            if reading is not None and reading.within(self._radius):
                return True
        return False


CONDITIONS: dict[str, type[Condition]] = {"person_at_stop": PersonAtStopCondition}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return the conditions of the integration."""
    return CONDITIONS
