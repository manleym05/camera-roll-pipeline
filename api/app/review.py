"""Endpoints for the phone review page. Every edit is also written to `feedback`."""

import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import require_reviewer
from app.captions import MEDIA_LIMIT, PLATFORMS, STYLES, carousel, move_posts, ordered
from app.config import settings
from app.db import get_session
from app.models import Candidate, Event, Feedback, Media, Post

router = APIRouter(prefix="/review", dependencies=[Depends(require_reviewer)])

STATUSES = {"pending", "confirmed", "dropped"}
SPRINT = {"pick", "backup", "none"}  # "none" clears it
# An event's pillar is the most common one among its top postable photos
PILLAR_PHOTOS = 5
POST_STATUSES = {"draft", "approved"}


def thumb_url(key: str | None) -> str | None:
    return f"{settings.thumb_base_url.rstrip('/')}/{key}" if key else None


class EventSummary(BaseModel):
    id: uuid.UUID
    kind: str
    start_at: datetime
    end_at: datetime
    label: str
    label_source: str
    ai_label: str | None
    summary: str | None
    status: str
    note: str | None
    media_count: int
    owners: dict
    were_you_there: bool
    candidates: int
    never_post: int
    cover: str | None
    # Platforms with drafted captions, and how many of those posts the owner approved
    drafted: int
    approved: int
    sprint: str | None
    pillar: str | None
    pillars: dict


class CandidateOut(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    thumb: str | None
    taken_at: datetime | None
    kind: str
    owner: str
    credit: str | None
    score: float | None
    rank: int | None
    manual_rank: int | None
    subscores: dict
    rationale: str | None
    never_post: bool
    never_post_reason: str | None


class PostOut(BaseModel):
    id: uuid.UUID
    platform: str
    status: str
    caption: str | None
    options: list[dict]
    picked: str | None
    media_ids: list[str]


class EventDetail(EventSummary):
    items: list[CandidateOut]
    posts: list[PostOut]


class PostPatch(BaseModel):
    caption: str | None = Field(default=None, max_length=2200)
    # Style of the option the owner tapped; the caption is set to that option's text
    pick: str | None = None
    status: str | None = None


class EventPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = None
    sprint: str | None = None
    note: str | None = Field(default=None, max_length=2000)


class MergeIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=2)


class OrderIn(BaseModel):
    candidate_ids: list[uuid.UUID]


class CandidatePatch(BaseModel):
    never_post: bool


def _posts(session: Session, event_id) -> list[Post]:
    posts = session.scalars(select(Post).where(Post.event_id == event_id)).all()
    return sorted(posts, key=lambda p: PLATFORMS.index(p.platform) if p.platform in PLATFORMS else len(PLATFORMS))


def _post_out(p: Post) -> PostOut:
    return PostOut(id=p.id, platform=p.platform, status=p.status, caption=p.caption, options=p.options or [],
                   picked=p.picked, media_ids=[str(i) for i in p.media_ids or []])


def _summary(session: Session, ev: Event, rows=None, posts=None) -> dict:
    rows = ordered(session, ev.id) if rows is None else rows
    posts = _posts(session, ev.id) if posts is None else posts
    cover = next((m.thumb_key for c, m in rows if not c.never_post), None)
    top = [(c.subscores or {}).get("pillar") for c, _ in rows if not c.never_post][:PILLAR_PHOTOS]
    pillars = Counter(p for p in top if p)
    # most common, ties to the higher-ranked photo
    pillar = max(pillars, key=lambda p: (pillars[p], -top.index(p))) if pillars else None
    return {
        **{k: getattr(ev, k) for k in ("id", "kind", "start_at", "end_at", "label", "label_source", "ai_label",
                                        "summary", "status", "note", "media_count", "owners")},
        "were_you_there": settings.primary_owner not in (ev.owners or {}),
        "candidates": len(rows),
        "never_post": sum(c.never_post for c, _ in rows),
        "cover": thumb_url(cover),
        "drafted": sum(bool(p.options) for p in posts),
        "approved": sum(p.status == "approved" for p in posts),
        "sprint": ev.sprint,
        "pillar": pillar,
        "pillars": dict(pillars),
    }


def _detail(session: Session, ev: Event) -> dict:
    rows = ordered(session, ev.id)
    posts = _posts(session, ev.id)
    items = [
        CandidateOut(
            id=c.id, media_id=m.id, thumb=thumb_url(m.thumb_key), taken_at=m.taken_at, kind=m.kind, owner=m.owner,
            credit=c.credit, score=c.score, rank=c.rank, manual_rank=c.manual_rank, subscores=c.subscores or {},
            rationale=c.rationale, never_post=c.never_post, never_post_reason=c.never_post_reason,
        )
        for c, m in rows
    ]
    return {**_summary(session, ev, rows, posts), "items": items, "posts": [_post_out(p) for p in posts]}


def _event(session: Session, event_id: uuid.UUID) -> Event:
    ev = session.get(Event, event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    return ev


def _feedback(session: Session, kind: str, event_id, payload: dict) -> None:
    session.add(Feedback(event_id=event_id, kind=kind, payload=payload))


@router.get("/events", response_model=list[EventSummary])
def list_events(session: Session = Depends(get_session)) -> list[dict]:
    return [_summary(session, ev) for ev in session.scalars(select(Event).order_by(Event.start_at))]


@router.get("/events/{event_id}", response_model=EventDetail)
def get_event(event_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    return _detail(session, _event(session, event_id))


@router.patch("/events/{event_id}", response_model=EventDetail)
def patch_event(event_id: uuid.UUID, body: EventPatch, session: Session = Depends(get_session)) -> dict:
    ev = _event(session, event_id)
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    if body.label is not None and body.label.strip() != ev.label:
        _feedback(session, "rename", ev.id, {"from": ev.label, "from_source": ev.label_source,
                                              "ai_label": ev.ai_label, "to": body.label.strip()})
        ev.label, ev.label_source = body.label.strip(), "manual"
    if body.status is not None and body.status != ev.status:
        _feedback(session, "status", ev.id, {"from": ev.status, "to": body.status})
        ev.status = body.status
    if body.sprint is not None:
        if body.sprint not in SPRINT:
            raise HTTPException(422, f"sprint must be one of {sorted(SPRINT)}")
        to = None if body.sprint == "none" else body.sprint
        if to != ev.sprint:
            _feedback(session, "sprint", ev.id, {"from": ev.sprint, "to": to})
            ev.sprint = to
    if body.note is not None and body.note != (ev.note or ""):
        _feedback(session, "note", ev.id, {"to": body.note})
        ev.note = body.note or None
    session.commit()
    return _detail(session, ev)


@router.post("/events/merge", response_model=EventDetail)
def merge_events(body: MergeIn, session: Session = Depends(get_session)) -> dict:
    """Fold the given events into the earliest one. Candidates and media move with them."""
    events = sorted((_event(session, i) for i in dict.fromkeys(body.ids)), key=lambda e: e.start_at)
    if len(events) < 2:
        raise HTTPException(422, "need two different events")
    target, others = events[0], events[1:]
    spans = list(target.merged_from or []) or [[target.start_at.isoformat(), target.end_at.isoformat()]]
    owners = dict(target.owners or {})
    post_ids = session.scalars(select(Post.id).where(Post.event_id.in_([e.id for e in events]))).all()
    for ev in others:
        session.execute(update(Media).where(Media.event_id == ev.id).values(event_id=target.id))
        session.execute(update(Candidate).where(Candidate.event_id == ev.id).values(event_id=target.id))
        session.execute(update(Feedback).where(Feedback.event_id == ev.id).values(event_id=target.id))
        spans += list(ev.merged_from or []) or [[ev.start_at.isoformat(), ev.end_at.isoformat()]]
        for o, n in (ev.owners or {}).items():
            owners[o] = owners.get(o, 0) + n
        target.start_at, target.end_at = min(target.start_at, ev.start_at), max(target.end_at, ev.end_at)
        if ev.kind == "trip":
            target.kind = "trip"
        if ev.status == "confirmed" or target.status == "dropped":
            target.status = ev.status
        if ev.sprint == "pick" or (ev.sprint and not target.sprint):
            target.sprint = ev.sprint
        session.flush()
        session.delete(ev)
    target.merged_from, target.owners = spans, owners
    move_posts(session, post_ids, target.id)
    target.media_count = session.scalar(select(func.count()).select_from(Media).where(Media.event_id == target.id))
    session.execute(update(Candidate).where(Candidate.event_id == target.id).values(manual_rank=None))
    _feedback(session, "merge", target.id, {"merged": [str(e.id) for e in others],
                                            "labels": [target.label] + [e.label for e in others], "spans": spans})
    session.commit()
    return _detail(session, target)


@router.put("/events/{event_id}/order", response_model=EventDetail)
def reorder(event_id: uuid.UUID, body: OrderIn, session: Session = Depends(get_session)) -> dict:
    ev = _event(session, event_id)
    rows = ordered(session, ev.id)
    by_id = {c.id: c for c, _ in rows}
    if set(body.candidate_ids) != set(by_id) or len(body.candidate_ids) != len(by_id):
        raise HTTPException(422, "candidate_ids must list every candidate of the event once")
    before = [str(c.id) for c, _ in rows]
    for n, cid in enumerate(body.candidate_ids, 1):
        by_id[cid].manual_rank = n
    _feedback(session, "reorder", ev.id, {
        "before": before,
        "after": [str(i) for i in body.candidate_ids],
        # Claude's rank per candidate, so ranking can learn where it disagreed with the owner
        "claude_rank": {str(c.id): c.rank for c, _ in rows},
    })
    session.commit()
    return _detail(session, ev)


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def patch_candidate(candidate_id: uuid.UUID, body: CandidatePatch, session: Session = Depends(get_session)) -> CandidateOut:
    c = session.get(Candidate, candidate_id)
    if c is None:
        raise HTTPException(404, "candidate not found")
    if c.never_post != body.never_post:
        _feedback(session, "never_post", c.event_id, {"candidate_id": str(c.id), "media_id": str(c.media_id),
                                                      "to": body.never_post, "claude_reason": c.never_post_reason})
        c.never_post = body.never_post
        session.commit()
    m = session.get(Media, c.media_id)
    return CandidateOut(
        id=c.id, media_id=m.id, thumb=thumb_url(m.thumb_key), taken_at=m.taken_at, kind=m.kind, owner=m.owner,
        credit=c.credit, score=c.score, rank=c.rank, manual_rank=c.manual_rank, subscores=c.subscores or {},
        rationale=c.rationale, never_post=c.never_post, never_post_reason=c.never_post_reason,
    )


@router.patch("/posts/{post_id}", response_model=EventDetail)
def patch_post(post_id: uuid.UUID, body: PostPatch, session: Session = Depends(get_session)) -> dict:
    """Pick one of Claude's options, edit the caption, or approve. Approving freezes the photos."""
    p = session.get(Post, post_id)
    if p is None:
        raise HTTPException(404, "post not found")
    options = {o["style"]: o["text"] for o in p.options or []}
    changes_text = body.pick is not None or body.caption is not None
    if changes_text and p.status != "draft" and body.status != "draft":
        raise HTTPException(409, "move the post back to draft before changing its caption")

    if body.pick is not None:
        if body.pick not in STYLES or body.pick not in options:
            raise HTTPException(422, f"pick must be one of {sorted(options)}")
        if p.caption != options[body.pick] or p.picked != body.pick:
            _feedback(session, "caption_pick", p.event_id, {"platform": p.platform, "style": body.pick,
                                                             "from": p.caption, "options": p.options})
        p.caption, p.picked = options[body.pick], body.pick
    elif body.caption is not None and body.caption.strip() != (p.caption or ""):
        text = body.caption.strip() or None
        _feedback(session, "caption_edit", p.event_id, {"platform": p.platform, "picked": p.picked,
                                                         "option": options.get(p.picked), "from": p.caption,
                                                         "to": text})
        p.caption = text
        if text is None:
            p.picked = None

    if body.status is not None and body.status != p.status:
        if body.status not in POST_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(POST_STATUSES)}")
        if body.status == "approved":
            if not p.caption:
                raise HTTPException(422, "pick or write a caption before approving")
            photos = carousel(session, p.event_id, MEDIA_LIMIT.get(p.platform, 10))
            if not photos:
                raise HTTPException(422, "no postable photos left for this event")
            p.media_ids = [str(m.id) for _, m in photos]
        else:
            p.media_ids = []
        _feedback(session, "caption_status", p.event_id, {
            "platform": p.platform, "from": p.status, "to": body.status, "picked": p.picked,
            # Whether the owner approved Claude's words as written: the main voice-tuning signal
            "edited": p.picked is None or p.caption != options.get(p.picked),
            "caption": p.caption,
        })
        p.status = body.status
    session.commit()
    return _detail(session, _event(session, p.event_id))


# --- Collections -------------------------------------------------------------
# A post built from photos across several events (a year in review, say), rather than
# one event's carousel. Post.event_id is null for these; the photos are the owner's picks,
# held in media_ids in his order.
#
# Meta allows 20 items in a carousel. The per-event posts stay on MEDIA_LIMIT's 10;
# a year's worth of photos needs more room than one afternoon's.
COLLECTION_LIMIT = {"instagram": 20, "x": 4}


class PhotoOut(BaseModel):
    candidate_id: uuid.UUID | None
    media_id: uuid.UUID
    thumb: str | None
    taken_at: datetime | None
    score: float | None
    pillar: str | None
    rationale: str | None
    event_id: uuid.UUID | None
    event_label: str | None


class CollectionOut(BaseModel):
    id: uuid.UUID
    platform: str
    status: str
    caption: str | None
    media_ids: list[str]
    photos: list[PhotoOut]


class CollectionIn(BaseModel):
    platform: str
    media_ids: list[uuid.UUID] = Field(default_factory=list)
    caption: str | None = Field(default=None, max_length=2200)


class CollectionPatch(BaseModel):
    media_ids: list[uuid.UUID] | None = None
    caption: str | None = Field(default=None, max_length=2200)
    status: str | None = None


def _photo(c: Candidate | None, m: Media, ev: Event | None) -> dict:
    return {
        "candidate_id": c.id if c else None,
        "media_id": m.id,
        "thumb": thumb_url(m.thumb_key),
        "taken_at": m.taken_at,
        "score": c.score if c else None,
        "pillar": (c.subscores or {}).get("pillar") if c else None,
        "rationale": c.rationale if c else None,
        "event_id": ev.id if ev else None,
        "event_label": ev.label if ev else None,
    }


def _collection(session: Session, p: Post) -> dict:
    """A collection with its photos resolved, kept in the order the owner put them in."""
    ids = [uuid.UUID(str(i)) for i in (p.media_ids or [])]
    media = {m.id: m for m in session.scalars(select(Media).where(Media.id.in_(ids)))} if ids else {}
    pairs: dict = {}
    if ids:
        for c, ev in session.execute(
            select(Candidate, Event).join(Event, Event.id == Candidate.event_id).where(Candidate.media_id.in_(ids))
        ).all():
            pairs.setdefault(c.media_id, (c, ev))
    photos = []
    for i in ids:
        m = media.get(i)
        if m is None:  # media deleted since it was picked
            continue
        c, ev = pairs.get(i, (None, None))
        photos.append(_photo(c, m, ev))
    return {"id": p.id, "platform": p.platform, "status": p.status, "caption": p.caption,
            "media_ids": [str(i) for i in ids], "photos": photos}


def _check_media(session: Session, platform: str, ids: list[uuid.UUID]) -> None:
    limit = COLLECTION_LIMIT.get(platform)
    if limit is None:
        raise HTTPException(422, f"platform must be one of {sorted(COLLECTION_LIMIT)}")
    if len(ids) > limit:
        raise HTTPException(422, f"{platform} takes at most {limit} photos, got {len(ids)}")
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "the same photo is in the list twice")
    if not ids:
        return
    ok = set(
        session.scalars(
            select(Candidate.media_id).where(Candidate.media_id.in_(ids), Candidate.never_post.is_(False))
        )
    )
    bad = [str(i) for i in ids if i not in ok]
    if bad:
        raise HTTPException(422, f"not postable (unknown or never-post): {', '.join(bad)}")


@router.get("/photos", response_model=list[PhotoOut])
def photos(start: date, end: date, session: Session = Depends(get_session)) -> list[dict]:
    """Every postable photo taken between two dates (inclusive), across events, best first."""
    lo = datetime.combine(start, time.min, tzinfo=timezone.utc)
    hi = datetime.combine(end, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    rows = session.execute(
        select(Candidate, Media, Event)
        .join(Media, Media.id == Candidate.media_id)
        .join(Event, Event.id == Candidate.event_id)
        .where(
            Candidate.never_post.is_(False),
            Event.status != "dropped",
            Media.taken_at >= lo,
            Media.taken_at < hi,
        )
        .order_by(Candidate.score.desc().nulls_last(), Media.taken_at)
    ).all()
    return [_photo(c, m, ev) for c, m, ev in rows]


@router.get("/collections", response_model=list[CollectionOut])
def list_collections(session: Session = Depends(get_session)) -> list[dict]:
    posts = session.scalars(
        select(Post).where(Post.event_id.is_(None)).order_by(Post.created_at)
    ).all()
    return [_collection(session, p) for p in posts]


@router.post("/collections", response_model=CollectionOut)
def create_collection(body: CollectionIn, session: Session = Depends(get_session)) -> dict:
    _check_media(session, body.platform, body.media_ids)
    p = Post(event_id=None, platform=body.platform, status="draft", options=[],
             media_ids=[str(i) for i in body.media_ids], caption=body.caption)
    session.add(p)
    session.commit()
    return _collection(session, p)


@router.patch("/collections/{post_id}", response_model=CollectionOut)
def patch_collection(post_id: uuid.UUID, body: CollectionPatch, session: Session = Depends(get_session)) -> dict:
    p = session.get(Post, post_id)
    if p is None or p.event_id is not None:
        raise HTTPException(404, "collection not found")
    if (body.media_ids is not None or body.caption is not None) and p.status != "draft" and body.status != "draft":
        raise HTTPException(409, "move the post back to draft before changing it")

    if body.media_ids is not None:
        _check_media(session, p.platform, body.media_ids)
        ids = [str(i) for i in body.media_ids]
        if ids != list(p.media_ids or []):
            session.add(Feedback(post_id=p.id, kind="collection_photos",
                                 payload={"platform": p.platform, "from": list(p.media_ids or []), "to": ids}))
            p.media_ids = ids
    if body.caption is not None and body.caption.strip() != (p.caption or ""):
        text = body.caption.strip() or None
        session.add(Feedback(post_id=p.id, kind="collection_caption",
                             payload={"platform": p.platform, "from": p.caption, "to": text}))
        p.caption = text

    if body.status is not None and body.status != p.status:
        if body.status not in POST_STATUSES:
            raise HTTPException(422, f"status must be one of {sorted(POST_STATUSES)}")
        if body.status == "approved":
            if not p.caption:
                raise HTTPException(422, "write a caption before approving")
            if not p.media_ids:
                raise HTTPException(422, "pick at least one photo before approving")
        session.add(Feedback(post_id=p.id, kind="collection_status",
                             payload={"platform": p.platform, "from": p.status, "to": body.status,
                                      "photos": len(p.media_ids or []), "caption": p.caption}))
        p.status = body.status
    session.commit()
    return _collection(session, p)
