"""Collections: one post built from photos across several events."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Candidate, Event, Feedback, Media, Post

JAN = datetime(2031, 1, 10, 12, tzinfo=timezone.utc)
JUL = datetime(2031, 7, 10, 12, tzinfo=timezone.utc)
NEXT = datetime(2032, 3, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app, headers={"Authorization": f"Bearer {settings.agent_token}"})
    app.dependency_overrides.clear()


def _event(db, start, n, label="Somewhere"):
    ev = Event(kind="local", start_at=start, end_at=start, label=label, label_source="claude",
               media_count=n, owners={"owner": n})
    db.add(ev)
    db.flush()
    cands = []
    for i in range(n):
        sha = uuid.uuid4().hex * 2
        m = Media(sha256=sha, source_path=sha, owner="owner", kind="photo", taken_at=start + timedelta(minutes=i),
                  tags={}, event_id=ev.id, thumb_key=f"thumbs/{sha}.jpg")
        db.add(m)
        db.flush()
        c = Candidate(event_id=ev.id, media_id=m.id, score=10 - i, rank=i + 1, subscores={"pillar": "travel"})
        db.add(c)
        cands.append((c, m))
    db.flush()
    return ev, cands


def test_photos_spans_events_and_filters_by_date(client, db):
    _event(db, JAN, 3, label="January")
    _event(db, JUL, 2, label="July")
    _event(db, NEXT, 4, label="Next year")

    r = client.get("/review/photos", params={"start": "2031-01-01", "end": "2031-12-31"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 5  # both 2025 events, nothing from 2026
    assert {x["event_label"] for x in rows} == {"January", "July"}
    scores = [x["score"] for x in rows]
    assert scores == sorted(scores, reverse=True)  # best first
    assert rows[0]["pillar"] == "travel"


def test_photos_excludes_never_post(client, db):
    _, cands = _event(db, JAN, 3)
    cands[0][0].never_post = True
    db.flush()
    rows = client.get("/review/photos", params={"start": "2031-01-01", "end": "2031-12-31"}).json()
    assert len(rows) == 2


def test_create_keeps_my_order(client, db):
    _, cands = _event(db, JAN, 3)
    ids = [str(m.id) for _, m in cands]
    picked = [ids[2], ids[0]]
    r = client.post("/review/collections", json={"platform": "instagram", "media_ids": picked})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media_ids"] == picked
    assert [p["media_id"] for p in body["photos"]] == picked
    assert body["status"] == "draft"


def test_rejects_too_many_and_duplicates(client, db):
    _, cands = _event(db, JAN, 6)
    ids = [str(m.id) for _, m in cands]
    assert client.post("/review/collections", json={"platform": "x", "media_ids": ids[:5]}).status_code == 422
    assert client.post("/review/collections",
                       json={"platform": "instagram", "media_ids": [ids[0], ids[0]]}).status_code == 422


def test_rejects_never_post_photo(client, db):
    _, cands = _event(db, JAN, 2)
    cands[0][0].never_post = True
    db.flush()
    r = client.post("/review/collections",
                    json={"platform": "instagram", "media_ids": [str(cands[0][1].id)]})
    assert r.status_code == 422
    assert "not postable" in r.json()["detail"]


def test_approve_needs_caption_and_photos(client, db):
    _, cands = _event(db, JAN, 2)
    ids = [str(m.id) for _, m in cands]
    empty = client.post("/review/collections", json={"platform": "instagram", "media_ids": []}).json()
    assert client.patch(f"/review/collections/{empty['id']}", json={"status": "approved"}).status_code == 422

    made = client.post("/review/collections", json={"platform": "instagram", "media_ids": ids}).json()
    assert client.patch(f"/review/collections/{made['id']}", json={"status": "approved"}).status_code == 422
    ok = client.patch(f"/review/collections/{made['id']}",
                      json={"caption": "A year of it", "status": "approved"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved"


def test_edits_write_feedback(client, db):
    _, cands = _event(db, JAN, 3)
    ids = [str(m.id) for _, m in cands]
    made = client.post("/review/collections", json={"platform": "instagram", "media_ids": ids[:2]}).json()
    client.patch(f"/review/collections/{made['id']}", json={"media_ids": [ids[2]], "caption": "Mine"})
    client.patch(f"/review/collections/{made['id']}", json={"status": "approved"})
    kinds = [f.kind for f in db.scalars(select(Feedback).where(Feedback.post_id == uuid.UUID(made["id"])))]
    assert "collection_photos" in kinds
    assert "collection_caption" in kinds
    assert "collection_status" in kinds


def test_approved_is_locked_until_back_to_draft(client, db):
    _, cands = _event(db, JAN, 2)
    ids = [str(m.id) for _, m in cands]
    made = client.post("/review/collections",
                       json={"platform": "instagram", "media_ids": ids, "caption": "Hi"}).json()
    client.patch(f"/review/collections/{made['id']}", json={"status": "approved"})
    assert client.patch(f"/review/collections/{made['id']}", json={"caption": "changed"}).status_code == 409
    assert client.patch(f"/review/collections/{made['id']}",
                        json={"caption": "changed", "status": "draft"}).status_code == 200


def test_collections_are_separate_from_event_posts(client, db):
    ev, cands = _event(db, JAN, 2)
    db.add(Post(event_id=ev.id, platform="instagram", status="draft", options=[], media_ids=[]))
    db.flush()
    made = client.post("/review/collections",
                       json={"platform": "instagram", "media_ids": [str(cands[0][1].id)]}).json()
    listed = client.get("/review/collections").json()
    ids = [c["id"] for c in listed]
    assert made["id"] in ids
    event_post = db.scalars(select(Post).where(Post.event_id == ev.id)).first()
    assert str(event_post.id) not in ids  # an event's post is not a collection
