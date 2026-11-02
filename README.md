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

A tracker is named by its line, the type of vehicle planned for the current trip and the fleet number, such as `11 - Autobus niskopodłogowy #1347`, or by the vehicle key when the source has no number. The type is the source's own description where it has one, as GZM does, in the source's language. Otherwise it is worked out from the mode, size and low floor, in your language, such as `11 - Low-floor articulated bus #1347`. The name follows the type when the next trip plans another vehicle, and the `vehicle_type` attribute states it. Its entity ID starts with `gtfs_hub_` and the provider, such as `device_tracker.gtfs_hub_gzm_11_1347`. Fleet numbers can repeat between carriers, so two trackers may share a name, even on one line. A tracker is unavailable while its vehicle is away or on another line, and is removed once the vehicle has been missing for 3 days of service. Time when Home Assistant is off or the feed shows no vehicles of your lines does not count. Each tracked line is also a device that links to its timetable.

### Recorder

A moving vehicle's tracker is written once per live update, every 30 seconds by default, so 30 vehicles store close to 90,000 states a day. Trip, headsign and direction are left out of the recorder, but the position stays, so the map card can show where a vehicle went. To keep no history of vehicles, exclude the trackers in `configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    entity_globs:
      - device_tracker.gtfs_hub_*
```

`device_tracker.gtfs_hub_gzm_*` excludes the trackers of one provider only. When an entry has 20 or more trackers and the recorder still stores them, Home Assistant shows a repair issue with this pattern. Trackers made by an earlier version are renamed to the prefixed form once, when the integration updates.

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

## Installation

Not possible yet: see Release status.

Once released, through the Marketplace (Home Assistant 2026.11 or later): open Settings, Marketplace, find GTFS Hub, install it and restart Home Assistant. With HACS on an older version, add this repository as a custom repository of type Integration. By hand: copy `custom_components/gtfs_hub` into your `config/custom_components` folder and restart.

## Setup

1. Add the integration and choose a source: an installed provider such as GZM, or any GTFS feed by URL, with an optional trip updates URL.
2. Choose your lines. Each mode has its own multi-select.
3. Choose stops of those lines. Each shows its direction, lines and code.
4. Optionally choose persons for the nearest stop sensor, change the live update interval, from 15 to 600 s with 30 s by default, and the delay threshold, from 1 to 60 minutes with 3 by default.

The entry's options repeat these steps, so you can change the selection at any time. A source given by URL is named after its host, such as `GTFS gtfs.ztp.krakow.pl`.

## Removal

Delete the entry under Settings, Devices & services, GTFS Hub. Its timetable cache and vehicle sightings are deleted with it. Then remove the integration in the Marketplace, or delete `custom_components/gtfs_hub`, and restart.

## Troubleshooting

- **No departures:** the stop shows departures of tracked lines only. Check the line is selected, and that the source's timetable covers today.
- **Live data is off:** the `Live data` sensor turns off when trip updates are missing or older than 5 minutes. GZM's feeds are empty at times in the evening and at night.
- **No vehicles on the map:** vehicles appear only for providers that declare a stable vehicle identity, such as GZM, and only on tracked lines.
- **Setup is slow:** the first download of a large source takes a minute or more.
- **Logs:** add `custom_components.gtfs_hub: debug` and `gtfs_hub: debug` under `logger: logs:` in `configuration.yaml`.

## Known limitations

- Vehicle types and pictures come from the vehicle planned for the trip; the one that comes may differ.
- Delay events need a feed that sends delays; a feed with stop times only gives departures but no events.
- Sources given by URL get no vehicles on the map, as their vehicle IDs are not proven stable.
- Lines served by frequencies rather than a timetable are not supported.

## Release status

Not released. What blocks it:

- The `gtfs-hub` and `gtfs-hub-gzm` packages are not on PyPI, so Home Assistant cannot install the requirements.
- The repository is private, with no topics and no GitHub release; the Marketplace and the HACS action read public repositories only.

## Verification

On 2026-11-02, the Docker test instance on Home Assistant 2026.10.4 was checked against live feeds: GZM with vehicles, names, line devices, delay events and the recorder issue; a source given by URL with Kraków's tram feed, with live delays at a pinned stop; the nearest stop of a person; and an abandoned setup, which left no files behind.

- `uv run pytest --cov=custom_components/gtfs_hub`: 99% coverage.
- hassfest: passes, run with `docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest`.
- HACS validation: not run, as it needs a public repository.
- Marketplace: not tested, as the test instance runs 2026.10.4.

## Test instance

`docker/up.sh` builds wheels of the local libraries and starts Home Assistant on http://localhost:8123, with this integration mounted read-only. After changing the integration, run `docker compose -f docker/compose.yaml restart`; after changing a library, run `docker/up.sh` again. To start from scratch, run `docker compose -f docker/compose.yaml down --volumes`.
