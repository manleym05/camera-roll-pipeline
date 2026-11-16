"""Inspect a photo export before ingesting it: is it complete, full quality, and does it keep metadata?

Shared albums, Messages and "Save Image" exports downscale photos and strip GPS — this catches that.
"""

from collections import Counter
from pathlib import Path

from brand_agent import imaging
from brand_agent.exif import read_many
from brand_agent.pipeline import MEDIA_EXT, _pair_live

FULL_RES_MP = 8.0  # iPhone originals are 12–48 MP; shared-album copies are ~3 MP


def check(folder: Path, expected: int | None = None) -> dict:
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXT)
    exifs = read_many(files)
    items, live = _pair_live(exifs)
    stills = [e for e in items if not imaging.is_video(e.path)]
    videos = [e for e in items if imaging.is_video(e.path)]
    mp = sorted((e.width or 0) * (e.height or 0) / 1e6 for e in stills if e.width)
    median_mp = mp[len(mp) // 2] if mp else 0.0
    dated = [e for e in items if e.taken_at]

    report = {
        "folder": str(folder),
        "files": len(files),
        "items": len(items),
        "stills": len(stills),
        "videos": len(videos),
        "live_photos": len(live),
        "types": dict(Counter(e.file_type for e in exifs)),
        "with_date": len(dated),
        "with_gps": sum(e.lat is not None for e in items),
        "median_megapixels": round(median_mp, 1),
        "date_range": [str(min(e.taken_at for e in dated).date()), str(max(e.taken_at for e in dated).date())]
        if dated
        else None,
        "camera_models": dict(Counter(e.model for e in items if e.model).most_common(5)),
    }

    problems = []
    if expected is not None and abs(len(items) - expected) > max(5, expected // 50):
        problems.append(f"expected about {expected} items, found {len(items)}")
    if stills and median_mp < FULL_RES_MP:
        problems.append(f"photos are downscaled (median {median_mp:.1f} MP) — export originals, not a shared-album copy")
    if items and report["with_gps"] < len(items) * 0.3:
        problems.append("most items have no GPS — location was stripped; use Options → All Photos Data")
    if items and len(dated) < len(items) * 0.9:
        problems.append("many items have no capture date")
    report["problems"] = problems
    report["ok"] = not problems
    return report
