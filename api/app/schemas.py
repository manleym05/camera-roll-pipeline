import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import EMBED_DIM


class KnownIn(BaseModel):
    sha256s: list[str]


class KnownOut(BaseModel):
    known: list[str]


class MediaIn(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    source_path: str
    owner: str = "owner"
    kind: str
    taken_at: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    make: str | None = None
    model: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    live_video_path: str | None = None
    thumb_key: str | None = None
    embedding: list[float] | None = Field(default=None, min_length=EMBED_DIM, max_length=EMBED_DIM)
    tags: dict = Field(default_factory=dict)
    sharpness: float | None = None
    dup_group: str | None = None
    is_dup_primary: bool = True
    face_count: int = 0
    excluded_reason: str | None = None


class MediaBatchIn(BaseModel):
    items: list[MediaIn]


class MediaBatchOut(BaseModel):
    inserted: int
    updated: int


class EventOut(BaseModel):
    id: uuid.UUID
    kind: str
    start_at: datetime
    end_at: datetime
    place: str | None
    label: str
    label_source: str
    media_count: int
    owners: dict
    note: str | None
