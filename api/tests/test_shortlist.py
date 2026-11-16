from datetime import datetime, timedelta, timezone

from app.shortlist import Item, select_candidates

T0 = datetime(2025, 8, 1, 8, tzinfo=timezone.utc)


def test_small_event_keeps_everything():
    items = [Item(i, T0 + timedelta(minutes=i), 100.0) for i in range(12)]
    assert len(select_candidates(items, 30)) == 12


def test_caps_and_returns_time_order():
    items = [Item(i, T0 + timedelta(minutes=i), float(i)) for i in range(100)]
    out = select_candidates(items, 30)
    assert len(out) == 30
    assert [i.taken_at for i in out] == sorted(i.taken_at for i in out)


def test_burst_does_not_swamp_the_timeline():
    # 80 sharp shots in one hour on day 1, 20 soft shots spread over days 2-5
    burst = [Item(f"b{i}", T0 + timedelta(seconds=40 * i), 5000.0) for i in range(80)]
    spread = [Item(f"s{i}", T0 + timedelta(days=1, hours=5 * i), 50.0) for i in range(20)]
    out = select_candidates(burst + spread, 30)
    later = [i for i in out if str(i.id).startswith("s")]
    assert len(later) >= 6


def test_pillar_tags_lift_soft_photos():
    plain = [Item(f"p{i}", T0 + timedelta(minutes=i), 1000.0) for i in range(40)]
    gym = [Item(f"g{i}", T0 + timedelta(minutes=i, seconds=30), 400.0, {"pillar:fitness": 0.9}) for i in range(5)]
    out = {i.id for i in select_candidates(plain + gym, 10)}
    assert sum(str(x).startswith("g") for x in out) >= 3
