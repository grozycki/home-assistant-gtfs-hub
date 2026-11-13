# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/); below 1.0, a release that needs you to act bumps the minor version.

## Unreleased

- The triggers `gtfs_hub.person_entered_stop` and `gtfs_hub.person_left_stop` and the condition `gtfs_hub.person_at_stop`, for a person reaching, leaving or being at a pinned stop, with no zone to draw. The trigger carries the stop's departures.
- Stop devices state the model ID `stop` and line devices `line`.

## 0.1.0 - 2026-11-11

First public release, installed as a custom repository.

- One entry per source: an installed provider such as GZM, or any GTFS feed by URL.
- Departure sensors for chosen stops, with realtime delays, cancellations and skipped stops.
- Vehicles of tracked lines on the map, with the arrow following the route shape, and fleet liveries.
- Delay events with a threshold, a live data sensor and the nearest stop of a person.
- The `gtfs_hub.get_departures` and `gtfs_hub.get_line_shapes` actions.
- Requires `gtfs-hub` 0.1.0 and `gtfs-hub-gzm` 0.1.0.
