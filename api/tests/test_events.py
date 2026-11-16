from datetime import date, datetime, timedelta, timezone

from app.config import Home
from app.events import Point, cluster, infer_home

HOME = (39.95, -75.16)
HOMES = [Home(lat=HOME[0], lon=HOME[1])]
LISBON = (38.72, -9.14)
T0 = datetime(2025, 5, 10, 9, tzinfo=timezone.utc)


def pts(start, n, step, where, near_home=False, gps=True):
    return [
        Point(f"{start:%j}-{i}", start + step * i, *(where if gps else (None, None)), near_home=near_home)
        for i in range(n)
    ]


def test_trip_away_from_home():
    out = cluster(pts(T0, 10, timedelta(hours=2), LISBON), HOMES, 100)
    assert [c.kind for c in out] == ["trip"]
    assert len(out[0].points) == 10


def test_long_gap_splits_trips():
    first = pts(T0, 9, timedelta(hours=1), LISBON)
    second = pts(T0 + timedelta(days=5), 9, timedelta(hours=1), LISBON)
    assert len(cluster(first + second, HOMES, 100)) == 2


def test_media_without_gps_joins_trip():
    mixed = pts(T0, 6, timedelta(hours=2), LISBON) + pts(T0 + timedelta(minutes=30), 4, timedelta(hours=2), LISBON, gps=False)
    out = cluster(mixed, HOMES, 100)
    assert len(out) == 1 and len(out[0].points) == 10


def test_local_burst_is_event_but_home_geofence_is_not():
    race = pts(T0, 15, timedelta(minutes=8), (HOME[0] + 0.02, HOME[1]))
    at_home = pts(T0 + timedelta(days=3), 15, timedelta(minutes=8), HOME, near_home=True)
    out = cluster(race + at_home, HOMES, 100)
    assert [c.kind for c in out] == ["local"]
    assert len(out[0].points) == 15


def test_small_clusters_dropped():
    assert cluster(pts(T0, 3, timedelta(hours=1), LISBON), HOMES, 100) == []


def test_infer_home_picks_most_common_cell():
    many = pts(T0, 20, timedelta(days=1), HOME) + pts(T0, 5, timedelta(hours=1), LISBON)
    (home,) = infer_home(many)
    assert (home.lat, home.lon) == (round(HOME[0], 1), round(HOME[1], 1))


def test_home_follows_a_move():
    columbus, raleigh = (39.96, -83.0), (35.79, -78.68)
    moved = [
        Home(lat=columbus[0], lon=columbus[1], end=date(2026, 3, 15)),
        Home(lat=raleigh[0], lon=raleigh[1], start=date(2026, 3, 15)),
    ]
    before = datetime(2025, 10, 1, 9, tzinfo=timezone.utc)
    after = datetime(2026, 6, 1, 9, tzinfo=timezone.utc)
    # Raleigh before the move is a trip; Raleigh after is a local event; Columbus after is a trip.
    out = cluster(
        pts(before, 12, timedelta(minutes=10), (raleigh[0] + 0.02, raleigh[1]))
        + pts(after, 12, timedelta(minutes=10), (raleigh[0] + 0.02, raleigh[1]))
        + pts(after + timedelta(days=20), 12, timedelta(minutes=10), columbus),
        moved,
        100,
    )
    assert [c.kind for c in out] == ["trip", "local", "trip"]
