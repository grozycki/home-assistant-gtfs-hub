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

On 2026-10-31, the Docker test instance on Home Assistant 2026.10.4 was set up through its interface against the live GZM feed: adding the source, line and stop, and choosing a person. Next departure, delay, live data and nearest stop sensors, and the `get_departures` action, returned sensible values.

- `uv run pytest --cov=custom_components/gtfs_hub`: 99% coverage, with `config_flow.py` at 100%.
- hassfest: passes, run with `docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest`.
- HACS validation: not run, as it needs a GitHub token and a public repository.

## Test instance

`docker/up.sh` builds wheels of the local libraries and starts Home Assistant on http://localhost:8123, with this integration mounted read-only. After changing the integration, run `docker compose -f docker/compose.yaml restart`; after changing a library, run `docker/up.sh` again. To start from scratch, run `docker compose -f docker/compose.yaml down --volumes`.
