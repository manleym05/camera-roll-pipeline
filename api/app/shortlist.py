"""Pre-filter each event to ~30 candidates before Claude sees them.

Eligible media: duplicate-group primaries, not excluded, outside the home geofence. Each item
gets a base score from sharpness (percentile within the event, so night shots aren't punished
against a sunny beach) and its strongest CLIP pillar tag. Selection takes the best item per time
slice of the event, then up to a third pillar-tagged shots, then the best of the rest.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Candidate, Event, Media

PER_EVENT = 30


def pillar_weights() -> dict[str, float]:
    """How much each pillar counts when shortlisting. Equal unless PILLAR_WEIGHTS says otherwise."""
    if settings.pillar_weights:
        return settings.pillar_weights
    n = len(settings.pillars) or 1
    return {p: 1.0 / n for p in settings.pillars}

# Share of picks reserved for timeline coverage; the rest go to the highest base scores.
SPREAD_SHARE = 0.5
PILLAR_TAG_MIN = 0.5


@dataclass
class Item:
    id: object
    taken_at: datetime
    sharpness: float | None
    tags: dict = field(default_factory=dict)
    kind: str = "photo"


def pillar_signal(tags: dict) -> float:
    """Strongest pillar tag, weighted by PILLAR_WEIGHTS. Scene tags with people get a nudge."""
    best = max(
        ((tags.get(f"pillar:{p}") or 0) * (0.6 + w) for p, w in pillar_weights().items()), default=0.0
    )
    people = max(tags.get("scene:selfie") or 0, tags.get("scene:people") or 0)
    return best + 0.3 * people


def base_scores(items: list[Item]) -> dict:
    sharp = sorted(math.log1p(i.sharpness) for i in items if i.sharpness is not None)

    def pct(v: float | None) -> float:
        if v is None or not sharp:
            return 0.5  # videos and unreadable frames: neutral
        lv = math.log1p(v)
        return sum(s <= lv for s in sharp) / len(sharp)

    return {i.id: 0.6 * pct(i.sharpness) + 0.4 * pillar_signal(i.tags or {}) for i in items}


def select_candidates(items: list[Item], n: int = PER_EVENT) -> list[Item]:
    """Up to n items, in time order, balancing quality, pillar fit and time spread."""
    items = sorted(items, key=lambda i: i.taken_at)
    if len(items) <= n:
        return items
    score = base_scores(items)
    chosen: set = set()
    # 1. Equal-duration slices of the event, so a 40-shot burst in one hour doesn't swamp the trip.
    slices = max(1, int(n * SPREAD_SHARE))
    t0, span = items[0].taken_at, (items[-1].taken_at - items[0].taken_at).total_seconds() or 1
    best: dict[int, Item] = {}
    for i in items:
        k = min(slices - 1, int((i.taken_at - t0).total_seconds() / span * slices))
        if k not in best or score[i.id] > score[best[k].id]:
            best[k] = i
    chosen.update(i.id for i in best.values())
    # 2. Pillar-tagged shots (gym, laptop, stage) are rare and on-topic: reserve room for them.
    tagged = [i for i in items if any((i.tags or {}).get(f"pillar:{p}", 0) >= PILLAR_TAG_MIN for p in settings.pillars)]
    for i in sorted(tagged, key=lambda i: score[i.id], reverse=True)[: n // 3]:
        if len(chosen) < n:
            chosen.add(i.id)
    # 3. Best of the rest.
    for i in sorted(items, key=lambda i: score[i.id], reverse=True):
        if len(chosen) >= n:
            break
        chosen.add(i.id)
    return [i for i in items if i.id in chosen]


def credit_for(owner: str) -> str | None:
    return None if owner == settings.primary_owner else settings.owner_names.get(owner, owner.capitalize())


def shortlist(session: Session, event_ids: list | None = None, n: int = PER_EVENT) -> dict:
    """(Re)build candidates per event. Rows for media that stay selected keep their scores."""
    q = select(Event).where(Event.status != "dropped")
    if event_ids:
        q = q.where(Event.id.in_(event_ids))
    counts = {}
    for ev in session.scalars(q).all():
        rows = session.execute(
            select(Media.id, Media.taken_at, Media.sharpness, Media.tags, Media.kind, Media.owner).where(
                Media.event_id == ev.id,
                Media.is_dup_primary.is_(True),
                Media.excluded_reason.is_(None),
                Media.near_home.is_(False),
                Media.thumb_key.is_not(None),
            )
        ).all()
        owners = {r.id: r.owner for r in rows}
        picked = select_candidates([Item(r.id, r.taken_at, r.sharpness, r.tags, r.kind) for r in rows], n)
        keep = {i.id for i in picked}
        existing = {c.media_id: c for c in session.scalars(select(Candidate).where(Candidate.event_id == ev.id))}
        stale = [mid for mid in existing if mid not in keep]
        if stale:
            session.execute(
                delete(Candidate).where(Candidate.event_id == ev.id, Candidate.media_id.in_(stale))
            )
        for i in picked:
            if i.id not in existing:
                session.add(Candidate(event_id=ev.id, media_id=i.id, credit=credit_for(owners[i.id])))
        counts[ev.id] = len(picked)
    session.commit()
    return {"events": len(counts), "candidates": sum(counts.values())}
