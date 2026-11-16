"""Group media into trips and local events.

Trips are runs of media far from the home lived in at the time (> HOME_TRIP_KM) with no gap longer than TRIP_GAP.
Local events are bursts near home: sessions with no gap longer than SESSION_GAP that hold
at least LOCAL_MIN media. Media inside the home geofence never forms an event.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.captions import move_posts
from app.config import Home, settings
from app.geo import km_from_home, place_name
from app.dedupe import collapse_across_owners
from app.models import Candidate, Event, EventLabel, Feedback, Media, Post

TRIP_GAP = timedelta(hours=36)
SESSION_GAP = timedelta(hours=3)
GPS_INHERIT_WINDOW = timedelta(hours=6)
TRIP_MIN = 5
LOCAL_MIN = 6
LABEL_SLACK = timedelta(hours=12)


@dataclass
class Point:
    id: object
    taken_at: datetime
    lat: float | None
    lon: float | None
    near_home: bool = False
    owner: str = "owner"


@dataclass
class Cluster:
    kind: str
    points: list[Point] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.points[0].taken_at

    @property
    def end(self) -> datetime:
        return self.points[-1].taken_at

    def gps(self) -> list[tuple[float, float]]:
        return [(p.lat, p.lon) for p in self.points if p.lat is not None]


def infer_home(points: list[Point]) -> list[Home]:
    """Most common ~10 km GPS cell as one open-ended home — a fallback when HOMES isn't set."""
    cells = Counter((round(p.lat, 1), round(p.lon, 1)) for p in points if p.lat is not None)
    if not cells:
        return []
    lat, lon = cells.most_common(1)[0][0]
    return [Home(lat=lat, lon=lon, label="inferred")]


def _away_flags(points: list[Point], homes: list[Home], trip_km: float) -> list[bool]:
    known: list[bool | None] = []
    for p in points:
        km = km_from_home(p.lat, p.lon, p.taken_at, homes)
        known.append(None if km is None else km > trip_km)
    # Media without GPS inherits from the nearest-in-time GPS neighbor, if close enough.
    flags = []
    for i, p in enumerate(points):
        if known[i] is not None:
            flags.append(known[i])
            continue
        best, best_dt = False, GPS_INHERIT_WINDOW
        for j in (i - 1, i + 1):
            k = j
            while 0 <= k < len(points) and known[k] is None:
                k += -1 if j < i else 1
            if 0 <= k < len(points):
                dt = abs(points[k].taken_at - p.taken_at)
                if dt <= best_dt:
                    best, best_dt = known[k], dt
        flags.append(best)
    return flags


def cluster(points: list[Point], homes: list[Home], trip_km: float) -> list[Cluster]:
    points = sorted(points, key=lambda p: p.taken_at)
    away = _away_flags(points, homes, trip_km)

    trips: list[Cluster] = []
    local_pool: list[Point] = []
    current: Cluster | None = None
    for p, is_away in zip(points, away):
        if is_away:
            if current is None or p.taken_at - current.end > TRIP_GAP:
                current = Cluster("trip")
                trips.append(current)
            current.points.append(p)
        elif not p.near_home:
            local_pool.append(p)

    sessions: list[Cluster] = []
    for p in local_pool:
        if not sessions or p.taken_at - sessions[-1].end > SESSION_GAP:
            sessions.append(Cluster("local"))
        sessions[-1].points.append(p)

    out = [c for c in trips if len(c.points) >= TRIP_MIN]
    out += [c for c in sessions if len(c.points) >= LOCAL_MIN]
    return sorted(out, key=lambda c: c.start)


def _geocode_label(c: Cluster) -> tuple[str | None, str]:
    gps = c.gps()
    if not gps:
        return None, f"Untitled {c.kind} - {c.start:%b %Y}"
    places = Counter(place_name(lat, lon) for lat, lon in gps[:: max(1, len(gps) // 20)])
    top = [name for name, _ in places.most_common(2)]
    return top[0], f"{' + '.join(top)} - {c.start:%b %Y}"


def _calendar_label(session: Session, c: Cluster) -> EventLabel | None:
    rows = session.scalars(
        select(EventLabel).where(
            EventLabel.start_at <= c.end + LABEL_SLACK,
            EventLabel.end_at >= c.start - LABEL_SLACK,
        )
    ).all()
    if not rows:
        return None

    def overlap(r: EventLabel) -> float:
        lo, hi = max(r.start_at, c.start - LABEL_SLACK), min(r.end_at, c.end + LABEL_SLACK)
        return (hi - lo).total_seconds()

    # Paper calendar is hand-written ground truth, so it wins ties with Google.
    return max(rows, key=lambda r: (r.source == "paper", overlap(r)))


CANDIDATE_FIELDS = ("media_id", "score", "rank", "rationale", "subscores", "never_post", "never_post_reason", "credit", "manual_rank")


def _cand_fields(c: Candidate) -> dict:
    return {f: getattr(c, f) for f in CANDIDATE_FIELDS}


def _match(c: Cluster, previous: list[Event]) -> Event | None:
    """The old event covering most of this cluster's span, if it covers over half."""
    span = (c.end - c.start).total_seconds() or 1
    best, best_share = None, 0.5
    for old in previous:
        shared = (min(old.end_at, c.end) - max(old.start_at, c.start)).total_seconds()
        if c.start == c.end and old.start_at <= c.start <= old.end_at:
            shared = span
        if shared / span > best_share:
            best, best_share = old, shared / span
    return best


def _remerge(clusters: list[Cluster], matches: list) -> tuple[list[Cluster], list]:
    """Neighbouring clusters that land in the same merged-by-the owner event become one again."""
    out_c, out_m = [], []
    for c, m in zip(clusters, matches):
        if out_c and m is not None and m is out_m[-1] and m.merged_from:
            prev = out_c[-1]
            prev.points = sorted(prev.points + c.points, key=lambda p: p.taken_at)
            if c.kind == "trip":
                prev.kind = "trip"
            continue
        out_c.append(c)
        out_m.append(m)
    return out_c, out_m


def shift_media(session: Session, event_ids: list, delta: timedelta, prefix: str | None = None) -> dict:
    """Move the capture time of an event's media by a fixed offset.

    For photos from a camera whose clock was set wrong: the offset is constant, so the
    shooting rhythm inside a day is preserved and only the instant moves. prefix
    limits it to one camera's files by filename (e.g. "DSCF"), since an event can hold
    media from several devices and only one of them may be wrong.

    Events are not re-timed here. Run rebuild afterwards: it re-clusters the moved
    media and keeps candidates, scores, the owner's order, status, merges, feedback and
    captions attached.
    """
    evs = [session.get(Event, i) for i in event_ids]
    if any(e is None for e in evs):
        missing = [str(i) for i, e in zip(event_ids, evs) if e is None]
        raise ValueError(f"no such event: {', '.join(missing)}")
    rows = session.scalars(select(Media).where(Media.event_id.in_([e.id for e in evs]))).all()
    if prefix:
        rows = [m for m in rows if (m.source_path or "").replace(chr(92), "/").rsplit("/", 1)[-1].upper().startswith(prefix.upper())]
    moved = [m for m in rows if m.taken_at is not None]
    before = min((m.taken_at for m in moved), default=None)
    for m in moved:
        m.taken_at = m.taken_at + delta
    for e in evs:
        session.add(Feedback(event_id=e.id, kind="media_shift", payload={
            "delta_seconds": delta.total_seconds(),
            "prefix": prefix,
            "label": e.label,
            "moved": sum(1 for m in moved if m.event_id == e.id),
        }))
    return {
        "events": len(evs),
        "matched": len(rows),
        "moved": len(moved),
        "undated": len(rows) - len(moved),
        "earliest_before": before.isoformat() if before else None,
        "earliest_after": (before + delta).isoformat() if before else None,
    }


def rebuild(session: Session) -> dict:
    cross_owner_dups = collapse_across_owners(session)
    media = session.execute(
        select(Media.id, Media.taken_at, Media.lat, Media.lon, Media.near_home, Media.owner).where(
            Media.excluded_reason.is_(None),
            Media.is_dup_primary.is_(True),
            Media.taken_at.is_not(None),
        )
    ).all()
    points = [Point(*row) for row in media]

    # Trips are relative to the owner's life; other owners' homes are only geofenced (at ingest).
    primary_homes = [h for h in settings.homes if h.owner == settings.primary_owner]
    if primary_homes:
        homes, home_source = primary_homes, "config"
    else:
        homes, home_source = infer_home(points), "inferred"

    clusters = cluster(points, homes, settings.home_trip_km)

    # Keep the owner's review work across rebuilds by matching each cluster to the old event it overlaps.
    previous = session.scalars(select(Event)).all()
    matches = [_match(c, previous) for c in clusters]
    clusters, matches = _remerge(clusters, matches)
    old_cands = {
        old.id: session.scalars(select(Candidate).where(Candidate.event_id == old.id)).all() for old in previous
    }
    old_cands = {k: [_cand_fields(c) for c in v] for k, v in old_cands.items()}
    old_feedback = {
        old.id: list(session.scalars(select(Feedback.id).where(Feedback.event_id == old.id))) for old in previous
    }
    old_posts = {old.id: list(session.scalars(select(Post.id).where(Post.event_id == old.id))) for old in previous}
    session.execute(update(Feedback).where(Feedback.event_id.is_not(None)).values(event_id=None))
    session.execute(update(Post).where(Post.event_id.is_not(None)).values(event_id=None))

    session.execute(update(Media).values(event_id=None))
    session.execute(delete(Event))

    for c, old in zip(clusters, matches):
        lats = [lat for lat, _ in c.gps()]
        lons = [lon for _, lon in c.gps()]
        place, label = _geocode_label(c)
        source = "geocode"
        cal = _calendar_label(session, c)
        if cal is not None:
            label, source = cal.title, cal.source
        ev = Event(
            kind=c.kind,
            start_at=c.start,
            end_at=c.end,
            lat=sum(lats) / len(lats) if lats else None,
            lon=sum(lons) / len(lons) if lons else None,
            place=place,
            label=label,
            label_source=source,
            media_count=len(c.points),
            owners=dict(Counter(p.owner for p in c.points)),
        )
        if old is not None:
            ev.note, ev.ai_label, ev.summary = old.note, old.ai_label, old.summary
            ev.status, ev.merged_from, ev.sprint = old.status, old.merged_from, old.sprint
            if old.label_source in ("manual", "claude"):
                ev.label, ev.label_source = old.label, old.label_source
        session.add(ev)
        session.flush()
        ids = {p.id for p in c.points}
        session.execute(update(Media).where(Media.id.in_(ids)).values(event_id=ev.id))
        if old is not None:
            for fields in old_cands[old.id]:
                if fields["media_id"] in ids:
                    session.add(Candidate(event_id=ev.id, **fields))
            if old_feedback[old.id]:
                session.execute(
                    update(Feedback).where(Feedback.id.in_(old_feedback[old.id])).values(event_id=ev.id)
                )
            old_feedback[old.id] = []  # a split event's feedback follows its first part
            if old_posts[old.id]:
                move_posts(session, old_posts[old.id], ev.id)
            old_posts[old.id] = []  # and so do its captions

    session.commit()
    return {
        "events": len(clusters),
        "trips": sum(c.kind == "trip" for c in clusters),
        "local": sum(c.kind == "local" for c in clusters),
        "media_considered": len(points),
        "cross_owner_dups": cross_owner_dups,
        "without_primary": sum(settings.primary_owner not in Counter(p.owner for p in c.points) for c in clusters),
        "home": home_source,
    }
