"""Claude scoring and event naming, decoupled from who runs the model.

    export: one packet per event (candidate labels + thumbnail paths) in data/scoring/
    a scorer (Claude Code subagents today; an API batch job later) writes <event_id>.result.json
    import: validate each result against EventScore and write candidates + event name

The scorer only ever sees `c1..cN` labels; packets map them back to media ids.
"""

import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, settings
from app.models import Candidate, Event, Media

SCORING_DIR = REPO_ROOT / "data" / "scoring"
THUMBS_DIR = REPO_ROOT / "data" / "storage"
# Weighted total, 0-10. Pillar mix steers pillar_fit inside the rubric, not here.
WEIGHTS = {"technical": 0.2, "owner": 0.25, "story": 0.25, "pillar_fit": 0.2, "variety": 0.1}
# Labels the owner controls; Claude's proposal is stored alongside but never replaces them.
KEEP_LABEL_SOURCES = {"manual"}

Score = Field(ge=0, le=10)


class CandidateScore(BaseModel):
    ref: str
    technical: float = Score
    owner: float = Score
    story: float = Score
    pillar: Literal["work", "tech", "fitness", "travel"]
    pillar_fit: float = Score
    variety: float = Score
    never_post: bool
    never_post_reason: str | None = None
    rationale: str


class EventScore(BaseModel):
    event_name: str
    summary: str
    carousel: list[str] = Field(max_length=10)
    candidates: list[CandidateScore]


def result_schema() -> dict:
    """JSON schema handed to the scorer (and usable as output_config.format on the API)."""
    return EventScore.model_json_schema()


def total(c: CandidateScore) -> float:
    return round(sum(getattr(c, k) * w for k, w in WEIGHTS.items()), 2)


def packet_path(event_id) -> Path:
    return SCORING_DIR / f"{event_id}.json"


def result_path(event_id) -> Path:
    return SCORING_DIR / f"{event_id}.result.json"


def export_packets(session: Session, only_unscored: bool = True) -> list[Path]:
    SCORING_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    events = session.scalars(select(Event).where(Event.status != "dropped").order_by(Event.start_at)).all()
    for ev in events:
        rows = session.execute(
            select(Candidate, Media)
            .join(Media, Media.id == Candidate.media_id)
            .where(Candidate.event_id == ev.id)
            .order_by(Media.taken_at)
        ).all()
        if not rows or (only_unscored and all(c.score is not None for c, _ in rows)):
            continue
        items = []
        for n, (c, m) in enumerate(rows, 1):
            tags = {k: round(v, 2) for k, v in (m.tags or {}).items() if k != "doc" and v >= 0.3}
            items.append({
                "ref": f"c{n}",
                "media_id": str(m.id),
                "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                "owner": m.owner,
                "kind": m.kind,
                "faces": m.face_count,
                "tags": tags,
                "thumb": str(THUMBS_DIR / m.thumb_key),
            })
        packet = {
            "event_id": str(ev.id),
            "kind": ev.kind,
            "start": ev.start_at.isoformat(),
            "end": ev.end_at.isoformat(),
            "current_label": ev.label,
            "place": ev.place,
            "owners": ev.owners,
            "candidates": items,
        }
        path = packet_path(ev.id)
        path.write_text(json.dumps(packet, indent=1), encoding="utf-8")
        out.append(path)
    return out


def scorer_prompt(packet: dict) -> str:
    """The user turn for one event: label lines, in the same order as the images."""
    lines = [
        f"Event: {packet['kind']}, {packet['start'][:10]} to {packet['end'][:10]}, "
        f"rough place: {packet['place'] or 'unknown'} (current label: {packet['current_label']}). "
        f"Photos per phone: {packet['owners']}. the owner is '{settings.primary_owner}'.",
        "",
    ]
    for c in packet["candidates"]:
        tags = ", ".join(f"{k.split(':')[1]} {v}" for k, v in c["tags"].items()) or "none"
        lines.append(f"{c['ref']}: {c['taken_at'][:16] if c['taken_at'] else '?'}, {c['kind']}, "
                     f"phone: {c['owner']}, faces: {c['faces']}, tags: {tags}")
    return "\n".join(lines)


def apply_result(session: Session, packet: dict, result: EventScore) -> dict:
    refs = {c["ref"]: uuid.UUID(c["media_id"]) for c in packet["candidates"]}
    scored = {c.ref: c for c in result.candidates if c.ref in refs}
    missing = sorted(set(refs) - set(scored), key=lambda r: int(r[1:]))
    if missing:
        raise ValueError(f"result is missing {missing}")

    event_id = uuid.UUID(packet["event_id"])
    ev = session.get(Event, event_id)
    if ev is None:
        raise ValueError(f"event {event_id} no longer exists (rebuilt?) - re-export")

    carousel = [r for r in dict.fromkeys(result.carousel) if r in scored and not scored[r].never_post]
    rest = sorted((r for r in scored if r not in carousel), key=lambda r: (scored[r].never_post, -total(scored[r])))
    order = {r: i for i, r in enumerate(carousel + rest, 1)}

    cands = {c.media_id: c for c in session.scalars(select(Candidate).where(Candidate.event_id == event_id))}
    for ref, s in scored.items():
        cand = cands.get(refs[ref])
        if cand is None:
            continue
        cand.score = total(s)
        cand.rank = order[ref]
        cand.rationale = s.rationale
        cand.never_post = s.never_post
        cand.never_post_reason = s.never_post_reason if s.never_post else None
        cand.subscores = {
            **{k: getattr(s, k) for k in WEIGHTS},
            "pillar": s.pillar,
            "carousel": carousel.index(ref) + 1 if ref in carousel else None,
        }

    ev.ai_label = result.event_name.strip()
    ev.summary = result.summary.strip()
    if ev.label_source not in KEEP_LABEL_SOURCES:
        ev.label, ev.label_source = ev.ai_label, "claude"
    session.commit()
    return {"event": ev.label, "scored": len(scored), "never_post": sum(s.never_post for s in scored.values())}


def import_results(session: Session, paths: list[Path] | None = None) -> list[dict]:
    paths = paths or sorted(SCORING_DIR.glob("*.result.json"))
    out = []
    for path in paths:
        event_id = path.name.removesuffix(".result.json")
        packet = json.loads(packet_path(event_id).read_text(encoding="utf-8"))
        try:
            result = EventScore.model_validate_json(path.read_text(encoding="utf-8"))
            out.append({"file": path.name, **apply_result(session, packet, result)})
        except (ValidationError, ValueError) as e:
            session.rollback()
            out.append({"file": path.name, "error": str(e).splitlines()[0]})
    return out
