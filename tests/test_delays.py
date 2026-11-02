"""Delay rules alone, with no Home Assistant."""

from datetime import UTC, date, datetime, timedelta

from custom_components.gtfs_hub.delays import DelayEventType, DelayRules

from gtfs_hub import LineKey, TripDelay, TripId

START = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
THRESHOLD = 180


def trip(delay: int | None, cancelled: bool = False, trip_id: str = "t1") -> TripDelay:
    return TripDelay(
        TripId(trip_id), date(2026, 11, 2), LineKey(3, "11"), "Terminus", delay, cancelled
    )


def run(rules: DelayRules, *ticks: list[TripDelay] | None) -> list[list[DelayEventType]]:
    fired = []
    for number, trips in enumerate(ticks):
        events = rules.update(trips, START + timedelta(seconds=30 * number))
        fired.append([event.type for event in events])
    return fired


def started() -> DelayRules:
    rules = DelayRules(THRESHOLD)
    rules.update([], START - timedelta(seconds=30))
    return rules


def test_delay_that_holds_fires_once() -> None:
    fired = run(started(), [trip(240)], [trip(240)], [trip(300)], [trip(600)])
    assert fired == [[], [DelayEventType.DELAYED], [], []]


def test_one_outlier_fires_nothing() -> None:
    assert run(started(), [trip(240)], [trip(60)], [trip(240)]) == [[], [], []]


def test_back_on_time_fires_recovered_once() -> None:
    fired = run(started(), [trip(240)], [trip(240)], [trip(30)], [trip(30)], [trip(0)])
    assert fired == [[], [DelayEventType.DELAYED], [], [DelayEventType.RECOVERED], []]


def test_hovering_at_the_threshold_stays_quiet() -> None:
    fired = run(started(), [trip(240)], [trip(240)], [trip(150)], [trip(150)], [trip(150)])
    assert fired == [[], [DelayEventType.DELAYED], [], [], []]


def test_low_threshold_recovers_at_zero() -> None:
    rules = DelayRules(60)
    rules.update([], START)
    fired = run(rules, [trip(90)], [trip(90)], [trip(30)], [trip(0)], [trip(0)])
    assert fired == [[], [DelayEventType.DELAYED], [], [], [DelayEventType.RECOVERED]]


def test_cancellation_fires_once() -> None:
    fired = run(started(), [trip(None, True)], [trip(None, True)], [trip(None, True)])
    assert fired == [[DelayEventType.CANCELLED], [], []]


def test_first_tick_only_records() -> None:
    rules = DelayRules(THRESHOLD)
    fired = run(rules, [trip(300), trip(None, True, "t2")], [trip(300)], [trip(300)])
    assert fired == [[], [], []]


def test_gap_in_live_data_pauses_and_breaks_the_streak() -> None:
    fired = run(started(), [trip(240)], None, [trip(240)], [trip(240)])
    assert fired == [[], [], [], [DelayEventType.DELAYED]]


def test_trip_leaving_the_feed_fires_nothing_and_is_forgotten() -> None:
    rules = started()
    run(rules, [trip(240)], [trip(240)])
    assert rules.update([], START + timedelta(minutes=30)) == []
    rules.update([], START + timedelta(hours=2))
    # Forgotten, so a new delay counts from scratch.
    fired = [
        [event.type for event in rules.update([trip(240)], START + timedelta(hours=2, seconds=s))]
        for s in (30, 60)
    ]
    assert fired == [[], [DelayEventType.DELAYED]]


def test_unknown_delay_breaks_a_streak() -> None:
    assert run(started(), [trip(240)], [trip(None)], [trip(240)]) == [[], [], []]


def test_reinstated_trip_counts_again() -> None:
    fired = run(started(), [trip(None, True)], [trip(240)], [trip(240)], [trip(None, True)])
    assert fired == [
        [DelayEventType.CANCELLED],
        [],
        [DelayEventType.DELAYED],
        [DelayEventType.CANCELLED],
    ]
