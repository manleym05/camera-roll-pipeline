"""Collapse burst shots and near-duplicates: same moment, near-identical CLIP embedding."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

WINDOW = timedelta(seconds=120)
SIMILARITY = 0.93


@dataclass
class Item:
    key: str
    taken_at: datetime | None
    embedding: np.ndarray
    sharpness: float


def group(items: list[Item]) -> dict[str, tuple[str, bool]]:
    """key -> (group id, is primary). Primary is the sharpest shot in its group."""
    timed = sorted((i for i in items if i.taken_at is not None), key=lambda i: i.taken_at)
    groups: list[list[Item]] = []
    for item in timed:
        placed = False
        # Only compare against recent groups — bursts are seconds apart.
        for g in reversed(groups[-5:]):
            if item.taken_at - g[-1].taken_at > WINDOW:
                continue
            if max(float(item.embedding @ other.embedding) for other in g) >= SIMILARITY:
                g.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])

    out: dict[str, tuple[str, bool]] = {}
    for g in groups:
        best = max(g, key=lambda i: i.sharpness)
        gid = best.key[:16]
        for i in g:
            out[i.key] = (gid, i is best)
    for i in items:
        out.setdefault(i.key, (i.key[:16], True))
    return out
