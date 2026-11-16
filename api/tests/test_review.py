import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import get_session
from app.main import app
from app.models import Candidate, Event, Feedback, Media

T0 = datetime(2020, 4, 19, 12, tzinfo=timezone.utc)


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app, headers={"Authorization": f"Bearer {settings.agent_token}"})
    app.dependency_overrides.clear()


def _event(db, start, n, owner="owner", label="Cabo San Lucas, MX - Apr 2020"):
    ev = Event(kind="trip", start_at=start, end_at=start + timedelta(days=2), label=label, label_source="claude",
               media_count=n, owners={owner: n})
    db.add(ev)
    db.flush()
    for i in range(n):
        sha = uuid.uuid4().hex * 2
        m = Media(sha256=sha, source_path=sha, owner=owner, kind="photo", taken_at=start + timedelta(hours=i),
                  tags={}, event_id=ev.id, thumb_key=f"thumbs/{sha}.jpg")
        db.add(m)
        db.flush()
        db.add(Candidate(event_id=ev.id, media_id=m.id, score=10 - i, rank=i + 1, subscores={}))
    db.flush()
    return ev


def _feedback(db, kind):
    return db.scalars(select(Feedback).where(Feedback.kind == kind)).all()


def test_review_requires_auth(db, monkeypatch):
    monkeypatch.setattr(settings, "review_auth", "access")
    app.dependency_overrides[get_session] = lambda: db
    try:
        r = TestClient(app).get("/review/events")
        assert r.status_code in (401, 503)
    finally:
        app.dependency_overrides.clear()


def test_rename_sets_manual_and_logs(client, db):
    ev = _event(db, T0, 3)
    r = client.patch(f"/review/events/{ev.id}", json={"label": "Cabo with Partner", "status": "confirmed"})
    assert r.status_code == 200
    body = r.json()
    assert (body["label"], body["label_source"], body["status"]) == ("Cabo with Partner", "manual", "confirmed")
    assert {f.kind for f in db.scalars(select(Feedback).where(Feedback.event_id == ev.id))} == {"rename", "status"}


def test_bad_status_rejected(client, db):
    ev = _event(db, T0, 1)
    assert client.patch(f"/review/events/{ev.id}", json={"status": "maybe"}).status_code == 422


def test_reorder_saves_manual_rank_and_feedback(client, db):
    ev = _event(db, T0, 3)
    items = client.get(f"/review/events/{ev.id}").json()["items"]
    new = [items[2]["id"], items[0]["id"], items[1]["id"]]
    r = client.put(f"/review/events/{ev.id}/order", json={"candidate_ids": new})
    assert [i["id"] for i in r.json()["items"]] == new
    (fb,) = [f for f in _feedback(db, "reorder") if f.event_id == ev.id]
    assert fb.payload["after"] == new and fb.payload["claude_rank"][new[0]] == 3
    # must list every candidate
    assert client.put(f"/review/events/{ev.id}/order", json={"candidate_ids": new[:2]}).status_code == 422


def test_never_post_toggle(client, db):
    ev = _event(db, T0, 2)
    cid = client.get(f"/review/events/{ev.id}").json()["items"][0]["id"]
    r = client.patch(f"/review/candidates/{cid}", json={"never_post": True})
    assert r.json()["never_post"] is True
    # flagged items sink below the rest
    assert client.get(f"/review/events/{ev.id}").json()["items"][-1]["id"] == cid
    assert len([f for f in _feedback(db, "never_post") if f.event_id == ev.id]) == 1


def test_merge_split_trip(client, db):
    a = _event(db, T0, 2)
    b = _event(db, T0 + timedelta(days=4), 3, owner="partner", label="Cabo San Lucas, MX - Apr 2020 (2)")
    r = client.post("/review/events/merge", json={"ids": [str(b.id), str(a.id)]})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(a.id) and len(body["items"]) == 5 and body["media_count"] == 5
    assert body["owners"] == {"owner": 2, "partner": 3}
    assert body["end_at"].startswith("2020-04-25")
    assert db.get(Event, b.id) is None
    assert len(db.get(Event, a.id).merged_from) == 2
    assert client.get(f"/review/events/{b.id}").status_code == 404


def test_were_you_there_flag(client, db):
    ev = _event(db, T0, 2, owner="partner")
    rows = {e["id"]: e for e in client.get("/review/events").json()}
    assert rows[str(ev.id)]["were_you_there"] is True


def test_sprint_pick_logs_and_clears(client, db):
    ev = _event(db, T0, 3)
    body = client.patch(f"/review/events/{ev.id}", json={"sprint": "pick"}).json()
    assert body["sprint"] == "pick"
    assert client.patch(f"/review/events/{ev.id}", json={"sprint": "maybe"}).status_code == 422
    assert client.patch(f"/review/events/{ev.id}", json={"sprint": "none"}).json()["sprint"] is None
    assert [f.payload["to"] for f in _feedback(db, "sprint") if f.event_id == ev.id] in (["pick", None], [None, "pick"])


def test_pillar_is_most_common_in_top_postable(client, db):
    ev = _event(db, T0, 4)
    cands = db.scalars(select(Candidate).where(Candidate.event_id == ev.id).order_by(Candidate.rank)).all()
    for c, p in zip(cands, ["travel", "fitness", "fitness", "work"]):
        c.subscores = {"pillar": p}
    db.flush()
    body = client.get(f"/review/events/{ev.id}").json()
    assert body["pillar"] == "fitness" and body["pillars"] == {"travel": 1, "fitness": 2, "work": 1}
    # never_post photos don't count
    cands[1].never_post = cands[2].never_post = True
    db.flush()
    assert client.get(f"/review/events/{ev.id}").json()["pillar"] == "travel"


def test_merge_keeps_the_sprint_pick(client, db):
    a, b = _event(db, T0, 2), _event(db, T0 + timedelta(days=5), 2)
    a.sprint, b.sprint = "backup", "pick"
    db.flush()
    body = client.post("/review/events/merge", json={"ids": [str(a.id), str(b.id)]}).json()
    assert body["sprint"] == "pick"
