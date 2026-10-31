# GTFS Hub for Home Assistant

Public transport departures from GTFS sources, built on [gtfs-hub](https://github.com/grozycki/gtfs-hub).

## What you get

- **Lines** you choose by search. They decide which stops the integration knows.
- **Pinned stops** with next departure and delay sensors.
- **A nearest stop sensor** per chosen person, listing departures of your lines from the closest stop within 2 km.
- **The `gtfs_hub.get_departures` action**, for boards on a dashboard.
- **A live data sensor**, off when departures show only the schedule.

Stops show departures of tracked lines only. The `timetable_url` attribute links to the source's page, where the source publishes one, for everything else that leaves the stop.

Delay reads unknown, not 0, when no prediction exists. Lists are left out of the recorder.

## Setup

1. Add the integration and choose a source.
2. Wait a moment for the schedule to download, then add lines from the entry's menu.
3. Optionally add stops, and choose persons in the entry's options.

## Status

Not installable yet: the `gtfs-hub` packages are not on PyPI. Vehicles on the map and delay events come in later stages.

## Verification

On 2026-10-31 a real Home Assistant 2026.10.4 core ran this integration against the live GZM feed, with bus 11, one pinned stop and one person. Setup took 0.09 s, the first sync 21 s, and live data, next departure, delay and nearest stop sensors all reported sensible values. The frontend, so the config flow screens, has been covered only by automated tests.

## Test instance

`docker/up.sh` builds wheels of the local libraries and starts Home Assistant on http://localhost:8123, with this integration mounted read-only. After changing the integration, run `docker compose -f docker/compose.yaml restart`; after changing a library, run `docker/up.sh` again. To start from scratch, run `docker compose -f docker/compose.yaml down --volumes`.
