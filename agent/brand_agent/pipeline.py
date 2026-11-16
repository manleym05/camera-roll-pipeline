"""Scan → hash → skip known → EXIF → pair Live Photos → filter → CLIP/sharpness/faces/thumb → dedupe → API."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from tqdm import tqdm

from brand_agent import dedupe, imaging, orient
from brand_agent.cache import Cache, sha256_file
from brand_agent.clip import Clip
from brand_agent.config import settings
from brand_agent.exif import Exif, read_many
from brand_agent.storage import get_storage

MEDIA_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".dng", ".mov", ".mp4", ".m4v"}
LIVE_MAX_S = 4.0
EMBED_BATCH = 32
POST_BATCH = 50


@dataclass
class Record:
    sha: str
    exif: Exif
    live_video: Path | None = None
    payload: dict = field(default_factory=dict)


@dataclass
class Report:
    scanned: int = 0
    unchanged: int = 0
    already_in_api: int = 0
    before_since: int = 0
    live_pairs: int = 0
    identical_copies: int = 0
    excluded: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    sent: int = 0
    dup_hidden: int = 0
    events: dict = field(default_factory=dict)


class Api:
    def __init__(self):
        self.client = httpx.Client(
            base_url=settings.api_url, headers={"Authorization": f"Bearer {settings.agent_token}"}, timeout=120
        )

    def known(self, shas: list[str]) -> set[str]:
        out: set[str] = set()
        for i in range(0, len(shas), 500):
            r = self.client.post("/ingest/known", json={"sha256s": shas[i : i + 500]})
            r.raise_for_status()
            out |= set(r.json()["known"])
        return out

    def send(self, items: list[dict]) -> None:
        for i in range(0, len(items), POST_BATCH):
            r = self.client.post("/ingest/media", json={"items": items[i : i + POST_BATCH]})
            r.raise_for_status()

    def rebuild_events(self) -> dict:
        r = self.client.post("/events/rebuild")
        r.raise_for_status()
        return r.json()


def _pair_live(exifs: list[Exif]) -> tuple[list[Exif], dict[Path, Path]]:
    """Drop the short MOV half of each Live Photo, returning still -> video."""
    stills = {e.content_id: e for e in exifs if not imaging.is_video(e.path) and e.content_id}
    by_stem = {(e.path.parent, e.path.stem.lower()): e for e in exifs if not imaging.is_video(e.path)}
    pairs: dict[Path, Path] = {}
    for e in exifs:
        if not imaging.is_video(e.path) or (e.duration_s or 0) > LIVE_MAX_S:
            continue
        still = stills.get(e.content_id) if e.content_id else None
        still = still or by_stem.get((e.path.parent, e.path.stem.lower()))
        if still:
            pairs[still.path] = e.path
    videos = set(pairs.values())
    return [e for e in exifs if e.path not in videos], pairs


def _excluded(e: Exif, doc_score: float | None) -> str | None:
    if e.file_type == "PNG" and not e.make:
        return "screenshot"
    if settings.doc_threshold is not None and doc_score is not None and doc_score >= settings.doc_threshold:
        return "document"
    return None


def run(
    folder: Path,
    limit: int | None = None,
    dry_run: bool = False,
    rebuild: bool = True,
    rescan: bool = False,
    owner: str = "owner",
    models: list[str] | None = None,
) -> Report:
    """rescan=True ignores the local cache and what the API already has, re-sending everything.
    models limits the run to files from those camera models (e.g. to reprocess one camera)."""
    report = Report()
    cache = Cache(settings.cache_path)
    api = None if dry_run else Api()
    storage = get_storage()
    clip = Clip()
    since = datetime.combine(settings.ingest_since, datetime.min.time())

    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXT)
    report.scanned = len(files)

    shas: dict[Path, str] = {}
    for p in tqdm(files, desc="hash", unit="file"):
        hit = cache.lookup(p)
        if hit and hit[1] in ("sent", "old") and not rescan:
            report.unchanged += 1
            continue
        shas[p] = hit[0] if hit else sha256_file(p)
    cache.commit()

    if api and shas and not rescan:
        known = api.known(list(set(shas.values())))
        for p in [p for p, s in shas.items() if s in known]:
            cache.mark(p, shas.pop(p), "sent")
            report.already_in_api += 1

    exifs = read_many(list(shas))
    exifs, live = _pair_live(exifs)
    if models:
        exifs = [e for e in exifs if e.model in models]
    report.live_pairs = len(live)
    for still, video in live.items():
        if video in shas:
            cache.mark(video, shas[video], "sent")

    records: list[Record] = []
    seen: set[str] = set()
    for e in exifs:
        if shas[e.path] in seen:  # byte-identical copy in another folder
            cache.mark(e.path, shas[e.path], "sent")
            report.identical_copies += 1
            continue
        seen.add(shas[e.path])
        if e.taken_at and e.taken_at.replace(tzinfo=None) < since:
            cache.mark(e.path, shas[e.path], "old")
            report.before_since += 1
            continue
        records.append(Record(sha=shas[e.path], exif=e, live_video=live.get(e.path)))
    records.sort(key=lambda r: (r.exif.taken_at is None, r.exif.taken_at.timestamp() if r.exif.taken_at else 0))
    if limit:
        records = records[:limit]

    done: list[Record] = []
    dedupe_items: list[dedupe.Item] = []
    for i in tqdm(range(0, len(records), EMBED_BATCH), desc="analyze", unit="batch"):
        batch, images = [], []
        for r in records[i : i + EMBED_BATCH]:
            try:
                im = imaging.load(r.exif.path, r.exif.duration_s)
                if not imaging.is_video(r.exif.path) and orient.needs_check(r.exif):
                    im = orient.upright(clip, im)
                images.append(im)
                batch.append(r)
            except Exception as exc:  # unreadable/corrupt file — report and move on
                report.failed.append(f"{r.exif.path.name}: {exc}")
        if not batch:
            continue
        embs = clip.embed(images)
        tags = clip.tags(embs)
        for r, im, emb, tag in zip(batch, images, embs, tags):
            doc = tag.pop("_doc")
            tag["doc"] = doc
            reason = _excluded(r.exif, doc)
            sharp = imaging.sharpness(im)
            thumb_key = None
            if reason is None:
                thumb_key = f"thumbs/{r.sha}.jpg"
                if not dry_run:
                    storage.put(thumb_key, imaging.thumbnail_jpeg(im), "image/jpeg")
            else:
                report.excluded[reason] = report.excluded.get(reason, 0) + 1
            e = r.exif
            r.payload = {
                "sha256": r.sha,
                "source_path": str(e.path),
                "owner": owner,
                "kind": "live" if r.live_video else ("video" if imaging.is_video(e.path) else "photo"),
                "taken_at": e.taken_at.isoformat() if e.taken_at else None,
                "lat": e.lat,
                "lon": e.lon,
                "make": e.make,
                "model": e.model,
                "width": e.width,
                "height": e.height,
                "duration_s": e.duration_s,
                "live_video_path": str(r.live_video) if r.live_video else None,
                "thumb_key": thumb_key,
                "embedding": [round(float(x), 6) for x in emb],
                "tags": tag,
                "sharpness": round(sharp, 2),
                "face_count": imaging.face_count(im) if reason is None else 0,
                "excluded_reason": reason,
            }
            done.append(r)
            if reason is None:
                dedupe_items.append(dedupe.Item(r.sha, e.taken_at, emb, sharp))

    groups = dedupe.group(dedupe_items)
    for r in done:
        gid, primary = groups.get(r.sha, (r.sha[:16], True))
        r.payload["dup_group"], r.payload["is_dup_primary"] = gid, primary
        report.dup_hidden += not primary

    if api and done:
        api.send([r.payload for r in done])
        for r in done:
            cache.mark(r.exif.path, r.sha, "sent")
        report.sent = len(done)
    cache.commit()

    if api and rebuild:
        report.events = api.rebuild_events()
    return report
