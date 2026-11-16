import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import events as event_detection
from app import review
from app.auth import require_reviewer
from app.config import REPO_ROOT, settings
from app.db import get_session
from app.geo import is_near_home
from app.models import Event, Media
from app.schemas import EventOut, KnownIn, KnownOut, MediaBatchIn, MediaBatchOut

app = FastAPI(title="Brand Engine API")
app.include_router(review.router)
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "PUT", "PATCH", "POST"],
        allow_headers=["*"],
    )

THUMBS = (REPO_ROOT / "data" / "storage" / "thumbs").resolve()


def require_agent(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, settings.agent_token):
        raise HTTPException(status_code=401, detail="bad agent token")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/ingest/known", response_model=KnownOut, dependencies=[Depends(require_agent)])
def known(body: KnownIn, session: Session = Depends(get_session)) -> KnownOut:
    rows = session.scalars(select(Media.sha256).where(Media.sha256.in_(body.sha256s)))
    return KnownOut(known=list(rows))


@app.post("/ingest/media", response_model=MediaBatchOut, dependencies=[Depends(require_agent)])
def ingest_media(body: MediaBatchIn, session: Session = Depends(get_session)) -> MediaBatchOut:
    if not body.items:
        return MediaBatchOut(inserted=0, updated=0)
    existing = set(
        session.scalars(select(Media.sha256).where(Media.sha256.in_([m.sha256 for m in body.items])))
    )
    # Identical files can appear twice in one batch; Postgres upserts each key only once.
    by_sha = {}
    for item in body.items:
        row = item.model_dump()
        row["near_home"] = is_near_home(item.lat, item.lon, item.taken_at)
        by_sha[item.sha256] = row
    rows = list(by_sha.values())
    stmt = insert(Media).values(rows)
    update_cols = {c: stmt.excluded[c] for c in rows[0] if c != "sha256"}
    session.execute(stmt.on_conflict_do_update(index_elements=["sha256"], set_=update_cols))
    session.commit()
    updated = len(existing)
    return MediaBatchOut(inserted=len(rows) - updated, updated=updated)


@app.post("/events/rebuild", dependencies=[Depends(require_agent)])
def rebuild_events(session: Session = Depends(get_session)) -> dict:
    return event_detection.rebuild(session)


@app.get("/events", response_model=list[EventOut], dependencies=[Depends(require_agent)])
def list_events(session: Session = Depends(get_session)) -> list[Event]:
    return list(session.scalars(select(Event).order_by(Event.start_at)))


@app.get("/thumbs/{name}", dependencies=[Depends(require_reviewer)])
def thumb(name: str) -> FileResponse:
    """EXIF-free thumbnails, for when the page isn't reading them from R2."""
    path = (THUMBS / name).resolve()
    if path.parent != THUMBS or path.suffix != ".jpg" or not path.is_file():
        raise HTTPException(404, "no such thumbnail")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
