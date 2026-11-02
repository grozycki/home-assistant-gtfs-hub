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

## Map

Pinned stops appear on the map with a stop sign marker. Vehicles of tracked lines appear when the provider declares a stable vehicle identity, as GZM does, or on a test instance with `GTFS_HUB_DEMO_VEHICLES=1`.

A tracker is named by its line and fleet number, such as `11 #1347`, or by the vehicle key when the source has no number. Fleet numbers can repeat between carriers, so two trackers may share a name, even on one line. A tracker is unavailable while its vehicle is away or on another line, and is removed once the vehicle has been missing for 3 days of service. Time when Home Assistant is off or the feed shows no vehicles of your lines does not count. Each tracked line is also a device that links to its timetable.

### Recorder

A moving vehicle's tracker is written once per live update, every 30 seconds by default, so 30 vehicles store close to 90,000 states a day. Trip, headsign and direction are left out of the recorder, but the position stays, so the map card can show where a vehicle went. To keep no history of vehicles, exclude the trackers of your lines in `configuration.yaml`, one pattern per line:

```yaml
recorder:
  exclude:
    entity_globs:
      - device_tracker.11_*
      - device_tracker.t6_*
```

A broader pattern such as `device_tracker.*` would also drop the history of phones and other trackers.

A vehicle marker shows the line, a picture by mode and size, and an arrow for its direction, worked out from movement. A white ring means a low floor. Pictures come from the vehicle planned for the trip, so a replacement may differ.

## Delay events

Each tracked line has a Delays event entity on the line's device, named after the line, such as `event.11_czeladz_wojkowicka_petla_piotrowice_petla_delays`. The examples below shorten it to `event.bus_11_delays`. It fires once per trip:

- `delayed` when the trip runs late by the delay threshold or more, 3 minutes by default, on two live updates in a row;
- `recovered` when a delayed trip is back to at most 2 minutes under the threshold, also on two updates in a row;
- `cancelled` when the feed cancels the trip.

The event carries `line`, `headsign`, `trip_id`, `start_date` and `delay` in seconds. Nothing fires on the first update after Home Assistant starts, or while live data is missing. A trip that leaves the feed fires nothing, so an automation should not wait for `recovered`. Change the threshold in the integration's options.

```yaml
triggers:
  - trigger: state
    entity_id: event.bus_11_delays
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.bus_11_delays
    attribute: event_type
    state: delayed
actions:
  - action: notify.notify
    data:
      message: >-
        Line {{ state_attr('event.bus_11_delays', 'line') }} to
        {{ state_attr('event.bus_11_delays', 'headsign') }} is
        {{ (state_attr('event.bus_11_delays', 'delay') / 60) | round }} min late
```

## Setup

1. Add the integration and choose a source.
2. Choose your lines. Each mode has its own multi-select.
3. Choose stops of those lines. Each shows its direction, lines and code.
4. Optionally choose persons for the nearest stop sensor.
5. Optionally change the live update interval, from 15 to 600 s. The default is 30 s.

The entry's options repeat these steps, so you can change the selection at any time.

## Status

Not installable yet: the `gtfs-hub` packages are not on PyPI. Vehicles on the map and delay events come in later stages.

## Verification

On 2026-10-31, the Docker test instance on Home Assistant 2026.10.4 was set up through its interface against the live GZM feed: adding the source, line and stop, and choosing a person. Next departure, delay, live data and nearest stop sensors, and the `get_departures` action, returned sensible values.

- `uv run pytest --cov=custom_components/gtfs_hub`: 99% coverage, with `config_flow.py` at 100%.
- hassfest: passes, run with `docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest`.
- HACS validation: not run, as it needs a GitHub token and a public repository.

## Test instance

`docker/up.sh` builds wheels of the local libraries and starts Home Assistant on http://localhost:8123, with this integration mounted read-only. After changing the integration, run `docker compose -f docker/compose.yaml restart`; after changing a library, run `docker/up.sh` again. To start from scratch, run `docker compose -f docker/compose.yaml down --volumes`.
