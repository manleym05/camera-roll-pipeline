import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.events import shift_media
from app.models import Event, Feedback, Media

T0 = datetime(2025, 1, 27, 9, 50, tzinfo=timezone.utc)
OFFSET = timedelta(days=-34, hours=5)


def _event(db, start, names):
    """An event holding one Media per filename, one hour apart."""
    ev = Event(kind="local", start_at=start, end_at=start + timedelta(hours=len(names)),
               label="Family Christmas at home", label_source="claude",
               media_count=len(names), owners={"owner": len(names)})
    db.add(ev)
    db.flush()
    for i, name in enumerate(names):
        sha = uuid.uuid4().hex * 2
        db.add(Media(sha256=sha, source_path=rf"C:\Users\you\Pictures\iPhone\202501_a\{name}",
                     owner="owner", kind="photo", taken_at=start + timedelta(hours=i), tags={},
                     event_id=ev.id, thumb_key=f"thumbs/{sha}.jpg"))
    db.flush()
    return ev


def _taken(db, ev):
    return {m.source_path.rsplit("\\", 1)[-1]: m.taken_at
            for m in db.scalars(select(Media).where(Media.event_id == ev.id))}


def test_shift_moves_every_photo_by_the_same_offset(db):
    ev = _event(db, T0, ["DSCF0025.JPG", "DSCF0027.JPG", "DSCF0031.JPG"])
    before = _taken(db, ev)
    out = shift_media(db, [ev.id], OFFSET)
    after = _taken(db, ev)
    assert out["moved"] == 3 and out["undated"] == 0
    assert all(after[n] - before[n] == OFFSET for n in before)
    # the Fuji roll lands on Christmas Eve, and the rhythm inside the day is preserved
    assert after["DSCF0025.JPG"] == datetime(2024, 12, 24, 14, 50, tzinfo=timezone.utc)
    assert after["DSCF0031.JPG"] - after["DSCF0025.JPG"] == timedelta(hours=2)


def test_prefix_leaves_the_other_camera_alone(db):
    ev = _event(db, T0, ["DSCF0025.JPG", "IMG_1130.JPG"])
    before = _taken(db, ev)
    out = shift_media(db, [ev.id], OFFSET, prefix="DSCF")
    after = _taken(db, ev)
    assert out["matched"] == 1 and out["moved"] == 1
    assert after["DSCF0025.JPG"] == before["DSCF0025.JPG"] + OFFSET
    assert after["IMG_1130.JPG"] == before["IMG_1130.JPG"]


def test_undated_media_is_counted_and_left_null(db):
    ev = _event(db, T0, ["DSCF0025.JPG"])
    db.add(Media(sha256=uuid.uuid4().hex * 2, source_path=r"X\DSCF0026.JPG", owner="owner",
                 kind="photo", taken_at=None, tags={}, event_id=ev.id, thumb_key="thumbs/x.jpg"))
    db.flush()
    out = shift_media(db, [ev.id], OFFSET)
    assert out["moved"] == 1 and out["undated"] == 1


def test_writes_one_feedback_row_per_event(db):
    ev = _event(db, T0, ["DSCF0025.JPG"])
    shift_media(db, [ev.id], OFFSET)
    # Scoped to this event: the laptop's database holds the real library, and real
    # shifts have been run on it, so a count across every media_shift row is not 1.
    rows = db.scalars(
        select(Feedback).where(Feedback.kind == "media_shift", Feedback.event_id == ev.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].event_id == ev.id
    assert rows[0].payload["delta_seconds"] == OFFSET.total_seconds()
    assert rows[0].payload["moved"] == 1


def test_unknown_event_is_rejected(db):
    with pytest.raises(ValueError, match="no such event"):
        shift_media(db, [uuid.uuid4()], OFFSET)
