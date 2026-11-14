# GTFS Hub for Home Assistant

Public transport departures from GTFS sources, built on [gtfs-hub](https://github.com/grozycki/gtfs-hub).

## What you get

- **Lines** you choose by search. They decide which stops the integration knows.
- **Pinned stops** with next departure and delay sensors.
- **A nearest stop sensor** per chosen person, listing departures of your lines from the closest stop within 2 km.
- **The `gtfs_hub.get_departures` action**, for boards on a dashboard.
- **The `gtfs_hub.get_line_shapes` action**, which returns the paths of a tracked line for drawing it on a map card.
- **A live data sensor**, off when departures show only the schedule.
- **Stop presence triggers and a condition**, for an automation that sends the next departures when you reach a pinned stop, with no zone to draw.

Stops show departures of tracked lines only. The `timetable_url` attribute links to the source's page, where the source publishes one, for everything else that leaves the stop.

Delay reads unknown, not 0, when no prediction exists. Lists are left out of the recorder.

Each listed departure carries `line_color` and `line_text_color`, for drawing its line as a badge: the provider's fleet colours when it has them, as GZM does, the source's line colours when they are readable, and white on the colour of the mode otherwise. Trackers state `route_type` and `provider`, and the shapes action names the provider, the line and its badge, so a card can find the vehicles of one line of one source.

## Map

Pinned stops appear on the map with a stop sign marker. Vehicles of tracked lines appear when the provider declares a stable vehicle identity, as GZM does, or on a test instance with `GTFS_HUB_DEMO_VEHICLES=1`.

A tracker is named by its line, the type of vehicle planned for the current trip and the fleet number, such as `11 - Autobus niskopodłogowy #1347`, or by the vehicle key when the source has no number. The type is the source's own description where it has one, as GZM does, in the source's language. Otherwise it is worked out from the mode, size and low floor, in your language, such as `11 - Low-floor articulated bus #1347`. The name follows the type when the next trip plans another vehicle, and the `vehicle_type` attribute states it. Its entity ID starts with `gtfs_hub_` and the provider, such as `device_tracker.gtfs_hub_gzm_11_1347`. Fleet numbers can repeat between carriers, so two trackers may share a name, even on one line. A tracker is unavailable while its vehicle is away or on another line, and is removed once the vehicle has been missing for 3 days of service. Time when Home Assistant is off or the feed shows no vehicles of your lines does not count. Each tracked line is also a device that links to its timetable.

The arrow on a marker shows the direction of travel. Where the source publishes the paths of its lines, as GZM does, the arrow follows the path where the vehicle is, from the first sight and while it stands. A vehicle more than 60 m from its path, or at a first sight on a street its line serves both ways, gets the direction from its movement instead, once it has moved 20 m. Either way the attribute is `derived_bearing`, as no source sends it. The built-in map shows points only, so the paths themselves need a card that draws them.

### Recorder

A moving vehicle's tracker is written once per live update, every 30 seconds by default, so 30 vehicles store close to 90,000 states a day. Trip, headsign and direction are left out of the recorder, but the position stays, so the map card can show where a vehicle went. To keep no history of vehicles, exclude the trackers in `configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    entity_globs:
      - device_tracker.gtfs_hub_*
```

`device_tracker.gtfs_hub_gzm_*` excludes the trackers of one provider only. When an entry has 20 or more trackers and the recorder still stores them, Home Assistant shows a repair issue with this pattern. Trackers made by an earlier version are renamed to the prefixed form once, when the integration updates.

A vehicle marker shows the line, a picture by mode and size, and an arrow for its direction. A ring in the body colour means a low floor. Pictures come from the vehicle planned for the trip, so a replacement may differ.

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

## Stop presence

Automations can react to a person, or a device tracker with GPS, reaching or leaving a pinned stop, with no zone for each stop:

- the trigger `gtfs_hub.person_entered_stop` fires when a target comes within the radius of a stop, 50 m by default and 10 to 500 m in all;
- the trigger `gtfs_hub.person_left_stop` fires when it goes more than 50 m beyond the radius, so a location jumping around the edge fires nothing;
- the condition `gtfs_hub.person_at_stop` passes while a target is within the radius of a stop.

In the automation editor they are under GTFS Hub. Pick one or more of the pinned stops, or leave the field empty for every pinned stop of every GTFS Hub entry, including stops you pin later. A target is at one stop at a time: it comes to the nearest stop whose radius it is within, and stays there until it leaves that stop. So the poles of one stop on both sides of a street notify once, and walking on to another stop fires leaving the first and entering the next. Both triggers take `for`, so walking or driving past the stop fires nothing, and the same `behavior` as zone triggers when they target several persons. `first` and `all` count the persons at the same stop. The condition takes `for` and `behavior` too. A reading with a GPS accuracy worse than the radius never brings a person in, and takes them out only when even its near edge is more than 50 m beyond the radius. Trackers without coordinates change nothing. Nothing fires when the automation is set up, so a restart at the stop sends no notification.

The trigger carries `entity_id`, `stop` with `key`, `name` and `code`, `distance` in metres, and `departures` of your lines from the stop, as the departure sensor lists them. This automation sends the next three departures on reaching any pinned stop, and clears them on leaving:

```yaml
triggers:
  - trigger: gtfs_hub.person_entered_stop
    id: entered
    target:
      entity_id: person.ania
    options:
      # No stop: every pinned stop. Pick stops in the editor to narrow it.
      for: "00:00:30"
  - trigger: gtfs_hub.person_left_stop
    id: left
    target:
      entity_id: person.ania
actions:
  - if:
      - condition: trigger
        id: entered
    then:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.stop.name }}"
          message: >-
            {% set rows = namespace(lines=[]) %}
            {% for item in trigger.departures[:3] %}
            {% set at = as_datetime(item.predicted or item.scheduled) %}
            {% set minutes = ((at - now()).total_seconds() / 60) | round(0, 'floor') | int %}
            {% set late = item.delay is number and item.delay >= 60 %}
            {% set when = item.status if item.status in ['cancelled', 'skipped']
              else minutes ~ ' min' ~ (' (+' ~ (item.delay / 60) | round | int ~ ')' if late else '') %}
            {% set rows.lines = rows.lines + [item.line ~ ' ' ~ item.headsign ~ ': ' ~ when] %}
            {% endfor %}
            {{ rows.lines | join('\n') }}
          data:
            tag: gtfs_hub_stop
    else:
      - action: notify.mobile_app_phone
        data:
          message: clear_notification
          data:
            tag: gtfs_hub_stop
```

Detection is as quick as the locations your phone sends. The Companion app reports in the background when you move a fair distance, which can be minutes apart on a short walk. Its location settings, such as high accuracy mode on Android, make it report more often.

Triggers and conditions of the integration are named subject, verb, object: `person_` for people at stops, with `vehicle_`, `departure_`, `trip_` and `live_data_` kept for later ones.

## Installation

GTFS Hub is not in the Marketplace catalogue yet, so add it from its repository:

[![Open your Home Assistant instance and open this repository.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=grozycki&repository=home-assistant-gtfs-hub&category=integration)

1. Open the link above. Or, on Home Assistant 2026.11 or later, go to Settings, Marketplace, Add from link and enter `https://github.com/grozycki/home-assistant-gtfs-hub`; on 2026.10, add it in HACS as a custom repository of type Integration. The first custom repository makes the Marketplace connect to GitHub once, with a code you enter on github.com.
2. Install GTFS Hub (Download in HACS) and restart Home Assistant.
3. Add it under Settings, Devices & services. On first use Home Assistant installs the `gtfs-hub` and `gtfs-hub-gzm` libraries from PyPI.

By hand: copy `custom_components/gtfs_hub` from the [latest release](https://github.com/grozycki/home-assistant-gtfs-hub/releases/latest) into your `config/custom_components` folder and restart.

Dashboard cards for departures and line maps are in [home-assistant-gtfs-hub-card](https://github.com/grozycki/home-assistant-gtfs-hub-card).

## Setup

1. Add the integration and choose a source: an installed provider such as GZM, or any GTFS feed by URL, with an optional trip updates URL.
2. Choose your lines. Each mode has its own multi-select.
3. Choose stops of those lines. Each shows its direction, lines and code.
4. Optionally choose persons for the nearest stop sensor, change the live update interval, from 15 to 600 s with 30 s by default, and the delay threshold, from 1 to 60 minutes with 3 by default.

The entry's options repeat these steps, so you can change the selection at any time. A source given by URL is named after its host, such as `GTFS gtfs.ztp.krakow.pl`.

## Removal

Delete the entry under Settings, Devices & services, GTFS Hub. Its timetable cache and vehicle sightings are deleted with it. Then remove the integration in the Marketplace or HACS, or delete `custom_components/gtfs_hub`, and restart.

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
- Stop presence follows the locations the phone sends, so a stop reached between two reports is noticed only on the next one.

## Versions

Releases follow [Semantic Versioning](https://semver.org/) and are listed in the [changelog](CHANGELOG.md). Below 1.0, a release that needs you to act, such as setting an entry up again, bumps the minor version and says so at the top of its notes. Each release pins the library versions it was tested with.

## Verification

On 2026-11-02, the Docker test instance on Home Assistant 2026.10.4 was checked against live feeds: GZM with vehicles, names, line devices, delay events and the recorder issue; a source given by URL with Kraków's tram feed, with live delays at a pinned stop; the nearest stop of a person; and an abandoned setup, which left no files behind.

On 2026-11-11, release 0.1.0 was installed from its GitHub release into a fresh Home Assistant 2026.10.4 container: Home Assistant fetched `gtfs-hub` and `gtfs-hub-gzm` 0.1.0 from PyPI on its own, and GZM set up with a pinned stop, live data and the vehicles of bus 840 with their arrows.

On 2026-11-12, release 0.1.0 was checked on a fresh Home Assistant 2026.11.1 container. It was installed by hand first, then through the Marketplace from the link above together with the cards; after a restart GZM loaded with departures, delays, live data and vehicles, and both cards drew from the resource the Marketplace added. The tests run on `pytest-homeassistant` 2026.11.1.

- `uv run pytest --cov=custom_components/gtfs_hub`: 99% coverage.
- hassfest: passes, run with `docker run --rm --platform linux/amd64 -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest`.
- CI runs tests, ruff, mypy, hassfest and HACS validation on every push.

## Test instance

`docker/up.sh` builds wheels of the local libraries and starts Home Assistant on http://localhost:8123, with this integration mounted read-only. After changing the integration, run `docker compose -f docker/compose.yaml restart`; after changing a library, run `docker/up.sh` again. To start from scratch, run `docker compose -f docker/compose.yaml down --volumes`.
