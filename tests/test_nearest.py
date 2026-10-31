from custom_components.gtfs_hub.nearest import distance, find_nearest

from gtfs_hub import Stop

# At this latitude, 0.001 degree of latitude is about 111 m.
NEAR = Stop("near", "Near", None, 50.0009, 19.0)
FAR = Stop("far", "Far", None, 50.05, 19.0)


def test_distance() -> None:
    assert round(distance(50, 19, 50.001, 19)) == 111


def test_person_near_a_stop() -> None:
    found = find_nearest(50.0, 19.0, [FAR, NEAR], None)
    assert found is not None
    assert found[0] is NEAR
    assert 95 < found[1] < 105


def test_beyond_the_limit() -> None:
    assert find_nearest(50.0, 19.0, [FAR], None) is None


def test_stop_without_position_is_ignored() -> None:
    assert find_nearest(50.0, 19.0, [Stop("x", "X", None, None, None)], None) is None


def test_keeps_current_stop_when_barely_beaten() -> None:
    held = Stop("held", "Held", None, 50.0011, 19.0)
    found = find_nearest(50.0, 19.0, [NEAR, held], "held")
    assert found is not None
    assert found[0] is held


def test_moves_when_clearly_beaten() -> None:
    held = Stop("held", "Held", None, 50.002, 19.0)
    found = find_nearest(50.0, 19.0, [NEAR, held], "held")
    assert found is not None
    assert found[0] is NEAR


def test_held_stop_beyond_the_limit_is_dropped() -> None:
    held = Stop("held", "Held", None, 50.0182, 19.0)
    close = Stop("close", "Close", None, 50.0178, 19.0)
    found = find_nearest(50.0, 19.0, [close, held], "held")
    assert found is not None
    assert found[0] is close
