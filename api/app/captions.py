"""Caption drafts, decoupled from who runs the model (same flow as scoring).

    export: one packet per event (event facts + the top photos of its carousel) in data/captions/
    a writer (Claude Code subagents today; an API batch job later) reads docs/voice.md and writes
    <event_id>.result.json with three options per platform
    import: validate each result against EventCaptions and store the options on the event's posts

Import never touches a caption the owner picked or wrote, or a post's status.
"""

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, settings
from app.models import Candidate, Event, Media, Post

CAPTIONS_DIR = REPO_ROOT / "data" / "captions"
THUMBS_DIR = REPO_ROOT / "data" / "storage"
VOICE = REPO_ROOT / "docs" / "voice.md"
STYLES = ("plain", "nerd", "self_own")
PLATFORMS = ("instagram", "x")
# Photos attached on approve: an Instagram carousel holds 10, an X post 4
MEDIA_LIMIT = {"instagram": 10, "x": 4}
TEXT_LIMIT = {"instagram": 2200, "x": 280}
PACKET_PHOTOS = 6
DASHES = ("—", "–")


class Option(BaseModel):
    style: Literal["plain", "nerd", "self_own"]
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def no_dashes(cls, v: str) -> str:
        if any(d in v for d in DASHES):
            raise ValueError("no em or en dashes (docs/voice.md)")
        return v.strip()


def _one_per_style(options: list[Option], platform: str) -> list[Option]:
    if sorted(o.style for o in options) != sorted(STYLES):
        raise ValueError(f"{platform} needs exactly one option per style {STYLES}")
    for o in options:
        if len(o.text) > TEXT_LIMIT[platform]:
            raise ValueError(f"{platform} {o.style} option is over {TEXT_LIMIT[platform]} characters")
    return sorted(options, key=lambda o: STYLES.index(o.style))


def _check_blocked(options: list[Option], terms: list[str], platform: str) -> None:
    """Reject a caption that names something this platform must never mention."""
    for term in terms:
        if any(term.lower() in o.text.lower() for o in options):
            raise ValueError(f"{term!r} is never named on {platform}")


class EventCaptions(BaseModel):
    instagram: list[Option]
    x: list[Option]

    @model_validator(mode="after")
    def check(self) -> "EventCaptions":
        self.instagram = _one_per_style(self.instagram, "instagram")
        self.x = _one_per_style(self.x, "x")
        _check_blocked(self.instagram, settings.instagram_blocked_terms, "Instagram")
        _check_blocked(self.x, settings.x_blocked_terms, "X")
        return self


def result_schema() -> dict:
    return EventCaptions.model_json_schema()


def packet_path(event_id) -> Path:
    return CAPTIONS_DIR / f"{event_id}.json"


def result_path(event_id) -> Path:
    return CAPTIONS_DIR / f"{event_id}.result.json"


def ordered(session: Session, event_id) -> list[tuple[Candidate, Media]]:
    """The event's candidates in review order: the owner's order, then Claude's rank."""
    return session.execute(
        select(Candidate, Media)
        .join(Media, Media.id == Candidate.media_id)
        .where(Candidate.event_id == event_id)
        .order_by(
            Candidate.manual_rank.nulls_last(),
            Candidate.never_post,
            Candidate.rank.nulls_last(),
            Media.taken_at,
        )
    ).all()


def carousel(session: Session, event_id, limit: int) -> list[tuple[Candidate, Media]]:
    return [(c, m) for c, m in ordered(session, event_id) if not c.never_post][:limit]


def move_posts(session: Session, post_ids, event_id) -> None:
    """Re-home posts onto an event (after a merge or rebuild), one per platform.

    An approved post wins over a draft. A post that would be a second one for its platform is left
    unlinked rather than deleted, so nothing the owner wrote is lost.
    """
    post_ids = list(post_ids)
    taken = set(session.scalars(select(Post.platform).where(Post.event_id == event_id, Post.id.not_in(post_ids))))
    posts = session.scalars(select(Post).where(Post.id.in_(post_ids))).all()
    for p in posts:
        p.event_id = None
    session.flush()
    for p in sorted(posts, key=lambda p: p.status != "approved"):
        if p.platform in taken:
            p.event_id = None
        else:
            p.event_id = event_id
            taken.add(p.platform)
    session.flush()


def export_packets(session: Session, only_missing: bool = True) -> list[Path]:
    CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    drafted = set(session.scalars(select(Post.event_id).where(Post.options != [])))
    out = []
    for ev in session.scalars(select(Event).where(Event.status != "dropped").order_by(Event.start_at)):
        if only_missing and ev.id in drafted:
            continue
        top = carousel(session, ev.id, MEDIA_LIMIT["instagram"])
        if not top or all(c.score is None for c, _ in top):
            continue
        photos = [
            {
                "ref": f"p{n}",
                "taken_at": m.taken_at.isoformat() if m.taken_at else None,
                "owner": m.owner,
                "credit": c.credit,
                "pillar": (c.subscores or {}).get("pillar"),
                "rationale": c.rationale,
                "thumb": str(THUMBS_DIR / m.thumb_key),
            }
            for n, (c, m) in enumerate(top[:PACKET_PHOTOS], 1)
        ]
        packet = {
            "event_id": str(ev.id),
            "kind": ev.kind,
            "start": ev.start_at.isoformat(),
            "end": ev.end_at.isoformat(),
            "label": ev.label,
            "summary": ev.summary,
            "note": ev.note,
            "place": ev.place,
            "owners": {settings.owner_names.get(o, o): n for o, n in (ev.owners or {}).items()},
            "pillars": dict(Counter(p["pillar"] for p in photos if p["pillar"])),
            "photos": photos,
        }
        path = packet_path(ev.id)
        path.write_text(json.dumps(packet, indent=1), encoding="utf-8")
        out.append(path)
    return out


def apply_result(session: Session, event_id: uuid.UUID, result: EventCaptions) -> dict:
    ev = session.get(Event, event_id)
    if ev is None:
        raise ValueError(f"event {event_id} no longer exists (rebuilt?) - re-export")
    posts = {p.platform: p for p in session.scalars(select(Post).where(Post.event_id == event_id))}
    for platform in PLATFORMS:
        options = [o.model_dump() for o in getattr(result, platform)]
        if platform in posts:
            posts[platform].options = options
        else:
            session.add(Post(event_id=event_id, platform=platform, status="draft", options=options, media_ids=[]))
    session.commit()
    return {"event": ev.label, "platforms": len(PLATFORMS)}


def import_results(session: Session, paths: list[Path] | None = None) -> list[dict]:
    paths = paths or sorted(CAPTIONS_DIR.glob("*.result.json"))
    out = []
    for path in paths:
        event_id = path.name.removesuffix(".result.json")
        try:
            result = EventCaptions.model_validate_json(path.read_text(encoding="utf-8"))
            out.append({"file": path.name, **apply_result(session, uuid.UUID(event_id), result)})
        except ValidationError as e:
            session.rollback()
            err = e.errors()[0]
            out.append({"file": path.name, "error": f"{'.'.join(map(str, err['loc']))}: {err['msg']}"})
        except ValueError as e:
            session.rollback()
            out.append({"file": path.name, "error": str(e).splitlines()[0]})
    return out
