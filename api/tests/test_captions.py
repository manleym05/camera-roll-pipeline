"""Caption validation, import and the review endpoints, against the local Docker Postgres. Rolled back after."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app import captions
from app.captions import EventCaptions, apply_result
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Candidate, Event, Feedback, Media, Post

T0 = datetime(2019, 7, 2, 12, tzinfo=timezone.utc)


def _opts(prefix="Chicago summers can't be beat!"):
    return [{"style": s, "text": f"{prefix} ({s})"} for s in ("plain", "nerd", "self_own")]


def _result(**over):
    return EventCaptions.model_validate({"instagram": _opts(), "x": _opts("Lakefront run"), **over})


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app, headers={"Authorization": f"Bearer {settings.agent_token}"})
    app.dependency_overrides.clear()


def _event(db, n=6, status="pending", never=(1,)):
    ev = Event(kind="trip", start_at=T0, end_at=T0 + timedelta(days=3), label="Chicago Fourth of July weekend",
               label_source="claude", summary="A long weekend in Chicago.", media_count=n, owners={"owner": n},
               status=status)
    db.add(ev)
    db.flush()
    for i in range(n):
        sha = uuid.uuid4().hex * 2
        m = Media(sha256=sha, source_path=sha, owner="owner", kind="photo", taken_at=T0 + timedelta(hours=i),
                  tags={}, event_id=ev.id, thumb_key=f"thumbs/{sha}.jpg")
        db.add(m)
        db.flush()
        db.add(Candidate(event_id=ev.id, media_id=m.id, score=10 - i, rank=i + 1, subscores={"pillar": "travel"},
                         never_post=i in never))
    db.flush()
    return ev


def test_one_option_per_style():
    with pytest.raises(ValidationError, match="one option per style"):
        _result(instagram=_opts()[:2])


def test_dashes_are_rejected():
    bad = _opts()
    bad[0]["text"] = "Hiked Pilot Mountain — not bad"
    with pytest.raises(ValidationError, match="dashes"):
        _result(instagram=bad)


def test_blocked_term_rejected_per_platform(monkeypatch):
    monkeypatch.setattr(settings, "instagram_blocked_terms", ["acme corp"])
    with pytest.raises(ValidationError, match="never named on Instagram"):
        _result(instagram=_opts("Offsite with the Acme Corp team"))
    # the same word is fine on a platform that does not block it
    assert _result(x=_opts("Offsite with the Acme Corp team")).x[0].style == "plain"


def test_x_length_limit():
    with pytest.raises(ValidationError, match="280"):
        _result(x=_opts("a" * 281))


def test_import_keeps_miles_choices(db):
    ev = _event(db)
    apply_result(db, ev.id, _result())
    posts = {p.platform: p for p in db.scalars(select(Post).where(Post.event_id == ev.id))}
    assert set(posts) == {"instagram", "x"} and [o["style"] for o in posts["x"].options] == ["plain", "nerd", "self_own"]
    posts["instagram"].caption, posts["instagram"].picked, posts["instagram"].status = "mine", "nerd", "approved"
    apply_result(db, ev.id, _result(instagram=_opts("Second draft")))
    ig = db.scalars(select(Post).where(Post.event_id == ev.id, Post.platform == "instagram")).one()
    assert (ig.caption, ig.picked, ig.status) == ("mine", "nerd", "approved")
    assert ig.options[0]["text"].startswith("Second draft")


def test_export_skips_dropped_and_drafted(db, tmp_path, monkeypatch):
    monkeypatch.setattr(captions, "CAPTIONS_DIR", tmp_path)
    keep, dropped, drafted = _event(db), _event(db, status="dropped"), _event(db)
    apply_result(db, drafted.id, _result())
    names = {p.name for p in captions.export_packets(db)}
    assert f"{keep.id}.json" in names
    assert f"{dropped.id}.json" not in names and f"{drafted.id}.json" not in names
    packet = json.loads((tmp_path / f"{keep.id}.json").read_text(encoding="utf-8"))
    # never_post photos are left out of what the writer sees
    assert len(packet["photos"]) == 5 and packet["pillars"] == {"travel": 5}


def test_pick_edit_approve(client, db):
    ev = _event(db)
    apply_result(db, ev.id, _result())
    x = db.scalars(select(Post).where(Post.event_id == ev.id, Post.platform == "x")).one()

    body = client.patch(f"/review/posts/{x.id}", json={"pick": "nerd"}).json()
    post = next(p for p in body["posts"] if p["platform"] == "x")
    assert (post["caption"], post["picked"]) == ("Lakefront run (nerd)", "nerd")
    assert body["drafted"] == 2 and body["approved"] == 0

    client.patch(f"/review/posts/{x.id}", json={"caption": "Lakefront run before the pub"})
    body = client.patch(f"/review/posts/{x.id}", json={"status": "approved"}).json()
    post = next(p for p in body["posts"] if p["platform"] == "x")
    assert post["status"] == "approved" and body["approved"] == 1
    # X takes 4 photos, in review order, skipping never_post
    ranked = [c["media_id"] for c in body["items"] if not c["never_post"]]
    assert post["media_ids"] == ranked[:4]

    assert client.patch(f"/review/posts/{x.id}", json={"pick": "plain"}).status_code == 409
    kinds = sorted(f.kind for f in db.scalars(select(Feedback).where(Feedback.event_id == ev.id)))
    assert kinds == ["caption_edit", "caption_pick", "caption_status"]
    status = db.scalars(select(Feedback).where(Feedback.kind == "caption_status", Feedback.event_id == ev.id)).one()
    assert status.payload["edited"] is True


def test_approve_needs_a_caption(client, db):
    ev = _event(db)
    apply_result(db, ev.id, _result())
    ig = db.scalars(select(Post).where(Post.event_id == ev.id, Post.platform == "instagram")).one()
    assert client.patch(f"/review/posts/{ig.id}", json={"status": "approved"}).status_code == 422


def test_merge_keeps_captions_and_approved_wins(client, db):
    first, second = _event(db), _event(db)
    second.start_at, second.end_at = T0 + timedelta(days=4), T0 + timedelta(days=5)
    apply_result(db, first.id, _result())
    apply_result(db, second.id, _result(instagram=_opts("Second half")))
    ig2 = db.scalars(select(Post).where(Post.event_id == second.id, Post.platform == "instagram")).one()
    client.patch(f"/review/posts/{ig2.id}", json={"pick": "plain", "status": "approved"})

    body = client.post("/review/events/merge", json={"ids": [str(first.id), str(second.id)]}).json()
    posts = {p["platform"]: p for p in body["posts"]}
    assert body["id"] == str(first.id) and set(posts) == {"instagram", "x"}
    assert posts["instagram"]["id"] == str(ig2.id) and posts["instagram"]["status"] == "approved"
    # the losing draft is unlinked, not deleted
    orphans = db.scalars(select(Post).where(Post.event_id.is_(None), Post.options != [])).all()
    assert {p.platform for p in orphans} >= {"instagram", "x"}


def test_rebuild_keeps_captions_attached(db, monkeypatch):
    """Rebuild deletes and recreates events; every linked post must follow its event (geocoder stubbed)."""
    from app import events

    monkeypatch.setattr(events, "_geocode_label", lambda c: (None, "Somewhere"))
    if db.scalar(select(func.count()).select_from(Event)) == 0:
        pytest.skip("no events in the local database")
    ev = db.scalars(select(Event).order_by(Event.start_at)).first()
    apply_result(db, ev.id, _result())
    linked = {p.id: p.platform for p in db.scalars(select(Post).where(Post.event_id.is_not(None)))}

    events.rebuild(db)

    after = {p.id: p.event_id for p in db.scalars(select(Post).where(Post.id.in_(list(linked))))}
    assert all(after[i] is not None for i in linked)
    assert db.scalar(select(func.count()).select_from(Post).where(Post.event_id.is_not(None))) == len(linked)
