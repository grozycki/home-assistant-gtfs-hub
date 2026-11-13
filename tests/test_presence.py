from custom_components.gtfs_hub.presence import at_stop, measure

# At this latitude, 0.001 degree of latitude is about 111 m.
STOP = (50.0, 19.0)
RADIUS = 50


def metres(north: float, accuracy: float | None = None) -> dict[str, object]:
    """Return tracker attributes of a person this many metres north of the stop."""
    attributes: dict[str, object] = {"latitude": 50.0 + north / 111_195, "longitude": 19.0}
    if accuracy is not None:
        attributes["gps_accuracy"] = accuracy
    return attributes


def walk(*readings: dict[str, object], start: bool = False) -> list[bool]:
    """Return whether the person is at the stop after each reading."""
    state = start
    seen = []
    for reading in readings:
        state = at_stop(state, measure(reading, *STOP), RADIUS)
        seen.append(state)
    return seen


def test_measures_the_distance() -> None:
    found = measure(metres(30, accuracy=12), *STOP)
    assert found is not None
    assert round(found.distance) == 30
    assert found.accuracy == 12


def test_missing_accuracy_counts_as_exact() -> None:
    found = measure(metres(30), *STOP)
    assert found is not None
    assert found.accuracy == 0


def test_walking_up() -> None:
    assert walk(metres(300), metres(30)) == [False, True]


def test_at_the_radius_counts_as_at_the_stop() -> None:
    assert walk(metres(49.9)) == [True]
    assert walk(metres(50.5)) == [False]


def test_jitter_at_the_edge_keeps_the_person_at_the_stop() -> None:
    assert walk(metres(55), metres(45), metres(60), start=True) == [True, True, True]


def test_jitter_outside_does_not_bring_the_person_in() -> None:
    assert walk(metres(55), metres(60), metres(90)) == [False, False, False]


def test_walking_away() -> None:
    assert walk(metres(150), start=True) == [False]


def test_leaving_needs_more_than_the_margin() -> None:
    assert walk(metres(99), start=True) == [True]
    assert walk(metres(101), start=True) == [False]


def test_coarse_fix_near_the_stop_changes_nothing() -> None:
    assert walk(metres(20, accuracy=200)) == [False]
    assert walk(metres(250, accuracy=200), start=True) == [True]


def test_coarse_fix_far_away_takes_the_person_out() -> None:
    assert walk(metres(2000, accuracy=200), start=True) == [False]
    assert walk(metres(301, accuracy=200), start=True) == [False]


def test_coarse_fix_never_brings_the_person_in() -> None:
    assert walk(metres(0, accuracy=51)) == [False]


def test_fix_as_precise_as_the_radius_counts() -> None:
    assert walk(metres(20, accuracy=50)) == [True]


def test_no_coordinates_change_nothing() -> None:
    assert measure({"source_type": "router"}, *STOP) is None
    assert walk({}, start=True) == [True]
    assert walk({}) == [False]


def test_non_numeric_coordinates_change_nothing() -> None:
    assert measure({"latitude": "50", "longitude": 19.0}, *STOP) is None
    assert measure({"latitude": True, "longitude": 19.0}, *STOP) is None
