"""Constants of the GTFS Hub integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "gtfs_hub"
PLATFORMS: Final = [Platform.BINARY_SENSOR, Platform.SENSOR]

CONF_PROVIDER: Final = "provider"
CONF_SCHEDULE_URL: Final = "schedule_url"
CONF_TRIP_UPDATES_URL: Final = "trip_updates_url"
CONF_PERSONS: Final = "persons"
CONF_QUERY: Final = "query"
CONF_LINE: Final = "line"
CONF_STOP: Final = "stop"
CONF_ROUTE_TYPE: Final = "route_type"
CONF_SHORT_NAME: Final = "short_name"
CONF_STOP_KEY: Final = "stop_key"
CONF_LIMIT: Final = "limit"
CONF_ENTRY_ID: Final = "config_entry_id"

GENERIC_PROVIDER: Final = "generic"
SUBENTRY_LINE: Final = "line"
SUBENTRY_STOP: Final = "stop"

SCHEDULE_INTERVAL: Final = timedelta(hours=6)
SCHEDULE_RETRY_INTERVAL: Final = timedelta(minutes=15)
DEPARTURES_INTERVAL: Final = timedelta(seconds=30)
LIST_LIMIT: Final = 5
