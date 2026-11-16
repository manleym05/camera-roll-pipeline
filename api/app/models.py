import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 512  # open_clip ViT-B-32


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Media(Base):
    """One photo or video from the camera roll. Originals stay on the PC; we keep metadata + thumb."""

    __tablename__ = "media"

    id: Mapped[uuid.UUID] = _id()
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_path: Mapped[str] = mapped_column(Text)
    # Whose camera roll it came from. Shared content is posted on the owner's brand.
    owner: Mapped[str] = mapped_column(String(32), default="owner", server_default="owner", index=True)
    kind: Mapped[str] = mapped_column(String(16))  # photo | video | live
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    make: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    live_video_path: Mapped[str | None] = mapped_column(Text)
    thumb_key: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBED_DIM), nullable=True)
    tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    sharpness: Mapped[float | None] = mapped_column(Float)
    dup_group: Mapped[str | None] = mapped_column(String(64), index=True)
    is_dup_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    near_home: Mapped[bool] = mapped_column(Boolean, default=False)
    # screenshot | document | null — excluded media is kept so re-ingest skips it
    excluded_reason: Mapped[str | None] = mapped_column(String(32))
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = _created()


class Event(Base):
    """A trip (away from home) or a local event (burst of media near home)."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(String(16))  # trip | local
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    place: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    label_source: Mapped[str] = mapped_column(String(16))  # calendar | paper | geocode | manual
    media_count: Mapped[int] = mapped_column(Integer, default=0)
    # {owner: media count}. Events without any of the owner's media are "were you there?" candidates.
    owners: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    note: Mapped[str | None] = mapped_column(Text)
    # Claude's proposed name + one-line summary. Applied to label unless the owner named it (manual).
    ai_label: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # pending | confirmed | dropped — the owner's call in the review page; survives rebuilds
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    # "30 moments" sprint: pick | backup | null. the owner's call; survives rebuilds and merges.
    sprint: Mapped[str | None] = mapped_column(String(16))
    # Start/end spans of events the owner merged into this one, so a rebuild can re-merge them.
    merged_from: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = _created()


class EventLabel(Base):
    """A dated entry from a calendar (Google ICS export or transcribed paper calendar)."""

    __tablename__ = "event_labels"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[uuid.UUID] = _id()
    source: Mapped[str] = mapped_column(String(16))  # calendar | paper
    external_id: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()


class Candidate(Base):
    """A media item shortlisted for an event's post, with Claude's score."""

    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("event_id", "media_id"),)

    id: Mapped[uuid.UUID] = _id()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    media_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    rationale: Mapped[str | None] = mapped_column(Text)
    # Per-criterion 0-10 scores, pillar and carousel slot from Claude
    subscores: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Private, medical, intimate or document-like: never offered for a post
    never_post: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    never_post_reason: Mapped[str | None] = mapped_column(Text)
    # Who took it, when not the owner (e.g. "Partner"); carried to the post caption
    credit: Mapped[str | None] = mapped_column(String(64))
    # the owner's order from the review page; wins over rank when set
    manual_rank: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Post(Base):
    """One platform's post for an event. Claude drafts options; the owner picks, edits and approves."""

    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("event_id", "platform"),)

    id: Mapped[uuid.UUID] = _id()
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(16))  # instagram | x | linkedin | youtube
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | queued | posted
    caption: Mapped[str | None] = mapped_column(Text)
    # Claude's drafts: [{"style": "plain" | "nerd" | "self_own", "text": ...}]
    options: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    # Style of the option the caption started from; null when the owner wrote it from scratch
    picked: Mapped[str | None] = mapped_column(String(16))
    media_ids: Mapped[list] = mapped_column(JSONB, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    buffer_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


class Feedback(Base):
    """the owner's reorders, picks and edits — training signal for ranking and voice."""

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = _id()
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    post_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
