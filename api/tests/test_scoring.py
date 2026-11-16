"""Scoring import against the local Docker Postgres; skipped when it isn't up. Rolled back after."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.events import Cluster, Point, _match, _remerge
from app.models import Candidate, Event, Media
from app.scoring import EventScore, apply_result, total

T0 = datetime(2020, 3, 1, 12, tzinfo=timezone.utc)


def _score(ref, base, never=False):
    return {"ref": ref, "technical": base, "owner": base, "story": base, "pillar": "travel",
            "pillar_fit": base, "variety": base, "never_post": never,
            "never_post_reason": "close-up of mouth" if never else None, "rationale": f"{ref} ok"}


def _setup(db, label_source="geocode", n=3):
    ev = Event(kind="trip", start_at=T0, end_at=T0 + timedelta(days=2), label="Boston Heights + Peninsula",
               label_source=label_source, media_count=n, owners={"owner": n})
    db.add(ev)
    db.flush()
    media = []
    for i in range(n):
        sha = uuid.uuid4().hex * 2
        m = Media(sha256=sha, source_path=sha, owner="partner" if i == 2 else "owner", kind="photo",
                  taken_at=T0 + timedelta(hours=i), tags={}, event_id=ev.id, thumb_key=f"thumbs/{sha}.jpg")
        db.add(m)
        media.append(m)
    db.flush()
    for m in media:
        db.add(Candidate(event_id=ev.id, media_id=m.id, credit="Partner" if m.owner == "partner" else None))
    db.flush()
    packet = {"event_id": str(ev.id), "candidates": [{"ref": f"c{i+1}", "media_id": str(m.id)} for i, m in enumerate(media)]}
    return ev, media, packet


def test_apply_ranks_carousel_first_and_flags(db):
    ev, media, packet = _setup(db)
    result = EventScore.model_validate({
        "event_name": "Cuyahoga Valley hike", "summary": "A fall hike.",
        "carousel": ["c3", "c1", "c2"],
        "candidates": [_score("c1", 6), _score("c2", 9, never=True), _score("c3", 5)],
    })
    apply_result(db, packet, result)
    cands = {c.media_id: c for c in db.scalars(select(Candidate).where(Candidate.event_id == ev.id))}
    # never_post items are dropped from the carousel and sink to the bottom
    assert [cands[m.id].rank for m in media] == [2, 3, 1]
    assert cands[media[1].id].never_post and cands[media[1].id].never_post_reason
    assert cands[media[2].id].credit == "Partner"
    assert cands[media[0].id].score == 6.0
    assert (ev.label, ev.label_source, ev.summary) == ("Cuyahoga Valley hike", "claude", "A fall hike.")


def test_manual_label_is_never_overwritten(db):
    ev, _, packet = _setup(db, label_source="manual")
    ev.label = "Dad's 60th"
    result = EventScore.model_validate({"event_name": "Lake weekend", "summary": "s", "carousel": [],
                                        "candidates": [_score(f"c{i}", 5) for i in (1, 2, 3)]})
    apply_result(db, packet, result)
    assert (ev.label, ev.label_source, ev.ai_label) == ("Dad's 60th", "manual", "Lake weekend")


def test_incomplete_result_is_rejected(db):
    ev, _, packet = _setup(db)
    result = EventScore.model_validate({"event_name": "x", "summary": "s", "carousel": [],
                                        "candidates": [_score("c1", 5)]})
    with pytest.raises(ValueError, match="missing"):
        apply_result(db, packet, result)


def test_scores_must_be_in_range():
    with pytest.raises(ValidationError):
        EventScore.model_validate({"event_name": "x", "summary": "s", "carousel": [],
                                   "candidates": [_score("c1", 11)]})


def test_total_weights_sum_to_ten_scale():
    assert total(EventScore.model_validate({"event_name": "x", "summary": "s", "carousel": [],
                                            "candidates": [_score("c1", 10)]}).candidates[0]) == 10.0


def _cluster(start, hours):
    return Cluster("trip", [Point(i, start + timedelta(hours=i), None, None) for i in range(hours)])


def test_rebuild_rematches_and_remerges_a_manual_merge():
    a, b = _cluster(T0, 10), _cluster(T0 + timedelta(days=4), 10)
    merged = Event(start_at=a.start, end_at=b.end, merged_from=[["x", "y"]])
    other = Event(start_at=T0 + timedelta(days=20), end_at=T0 + timedelta(days=21), merged_from=[])
    matches = [_match(c, [merged, other]) for c in (a, b)]
    assert matches == [merged, merged]
    clusters, out = _remerge([a, b], matches)
    assert len(clusters) == 1 and len(clusters[0].points) == 20 and out == [merged]
    # without a recorded merge, two clusters inside one old event stay apart
    merged.merged_from = []
    assert len(_remerge([_cluster(T0, 10), _cluster(T0 + timedelta(days=4), 10)], matches)[0]) == 2
