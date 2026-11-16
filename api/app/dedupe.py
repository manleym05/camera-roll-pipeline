"""Collapse the same moment shot (or shared) by two people.

The agent collapses bursts within one camera roll. Across rolls, a photo Partner AirDropped
is byte-identical and never re-ingested, but a re-saved or re-shot copy differs in bytes, so
it is matched here by time + CLIP similarity and the sharper copy is kept.
"""

from datetime import timedelta

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Media

WINDOW = timedelta(seconds=120)
SIMILARITY = 0.93


def collapse_across_owners(session: Session) -> int:
    rows = session.execute(
        select(Media.id, Media.owner, Media.taken_at, Media.embedding, Media.sharpness, Media.dup_group)
        .where(
            Media.excluded_reason.is_(None),
            Media.is_dup_primary.is_(True),
            Media.taken_at.is_not(None),
            Media.embedding.is_not(None),
        )
        .order_by(Media.taken_at)
    ).all()
    if len({r.owner for r in rows}) < 2:
        return 0

    alive = [True] * len(rows)
    embs = [np.asarray(r.embedding, dtype=np.float32) for r in rows]
    hidden = 0
    for i, r in enumerate(rows):
        if not alive[i]:
            continue
        j = i + 1
        while j < len(rows) and rows[j].taken_at - r.taken_at <= WINDOW:
            other = rows[j]
            if alive[j] and other.owner != r.owner and float(embs[i] @ embs[j]) >= SIMILARITY:
                keep, drop = (i, j) if (r.sharpness or 0) >= (other.sharpness or 0) else (j, i)
                session.execute(
                    update(Media)
                    .where(Media.id == rows[drop].id)
                    .values(is_dup_primary=False, dup_group=rows[keep].dup_group)
                )
                alive[drop] = False
                hidden += 1
                if drop == i:
                    break
            j += 1
    session.commit()
    return hidden
