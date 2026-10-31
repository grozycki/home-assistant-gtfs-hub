"""The library must parse with the realtime bindings Home Assistant pins."""

from google.transit import gtfs_realtime_pb2 as pb  # type: ignore[import-untyped]  # pin has no stubs
from gtfs_hub.realtime import parse_trip_updates


def test_parse_with_pinned_bindings() -> None:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    entity = feed.entity.add()
    entity.id = "1"
    entity.trip_update.trip.trip_id = "t1"
    entity.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED
    assert parse_trip_updates(feed.SerializeToString()).trips["t1"].cancelled
