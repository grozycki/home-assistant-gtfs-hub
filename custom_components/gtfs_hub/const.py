"""Constants of the GTFS Hub integration."""

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "gtfs_hub"
PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
]

# Shows vehicles on a test instance before their identity is proven.
DEMO_VEHICLES_VARIABLE: Final = "GTFS_HUB_DEMO_VEHICLES"

CONF_PROVIDER: Final = "provider"
CONF_SCHEDULE_URL: Final = "schedule_url"
CONF_TRIP_UPDATES_URL: Final = "trip_updates_url"
CONF_PERSONS: Final = "persons"
CONF_LIVE_INTERVAL: Final = "live_interval"
CONF_DELAY_THRESHOLD: Final = "delay_threshold"
CONF_STOPS: Final = "stops"
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
MIN_LIVE_INTERVAL: Final = 15
MAX_LIVE_INTERVAL: Final = 600
LIST_LIMIT: Final = 5
# Minutes. Feeds move delays in steps, so a low threshold gives noise.
DEFAULT_DELAY_THRESHOLD: Final = 3
MIN_DELAY_THRESHOLD: Final = 1
MAX_DELAY_THRESHOLD: Final = 60
