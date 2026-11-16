"""Turn a collection's originals into files ready to drag into a posting tool.

Instagram wants JPEG, and most of the library is HEIC from an iPhone, so the HEIC files
have to be re-encoded. That re-encode is done from pixels, which drops every piece of
metadata including GPS. Files that are already JPEG are copied losslessly through
exiftool instead, with only the location tags removed, so nothing is re-compressed.

Which way up a photo goes is decided by the thumbnail, not by the original. Most files
say so themselves, in the EXIF Orientation tag, but a few are stored sideways with no tag
at all, and nothing inside those files reveals it. Their thumbnails were corrected during
review, and that correction was never written back to the original or to the database, so
the thumbnail is the only record of it. It is also the image the owner saw when he picked the
photo, which makes it the right thing to agree with.

Output names are numbered (01_, 02_, ...) because upload tools order by filename, and
the order the owner put the photos in is the carousel order.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from brand_agent import exif, imaging

LOSSLESS = {".jpg", ".jpeg"}
QUALITY = 95
ORIENTATION = 274  # EXIF tag
# Both images are reduced to this square before they are compared, so that a candidate
# with the wrong aspect ratio gets squashed and scores badly. That is the signal.
PROBE = 64
# A turn has to beat leaving the photo alone by this much to be applied. Near-symmetric
# photos score all four ways about equally, and on those the file is the better guess.
MARGIN = 0.85


def _upright(path: Path) -> bool:
    """True when a JPEG needs no rotation, so it can be copied rather than re-encoded.

    A JPEG that carries an Orientation tag is stored rotated, with the tag telling viewers
    to turn it. Copying keeps that tag, so anything that ignores it (and anything measuring
    width against height, including carousel crop maths) sees the photo on its side.
    Those are re-encoded so the rotation lives in the pixels.
    """
    try:
        with Image.open(path) as im:
            return im.getexif().get(ORIENTATION, 1) == 1
    except Exception:
        return False


def _probe(im: Image.Image) -> np.ndarray:
    """A small square grayscale copy, so two images compare regardless of size or aspect."""
    return np.asarray(im.convert("L").resize((PROBE, PROBE)), dtype=np.int16)


def _match_thumbnail(im: Image.Image, thumb: Image.Image) -> tuple[Image.Image, int]:
    """Turn `im` to whichever rotation matches `thumb`. Returns the image and the turn made.

    The turn is clockwise degrees, 0 when the image was already right or when no rotation
    was a clear enough winner to risk it.
    """
    ref = _probe(thumb)
    # PIL's ROTATE_n turns counter-clockwise, so the constants run backwards against the
    # clockwise degrees we report.
    candidates = {0: im, 90: im.transpose(Image.ROTATE_270),
                  180: im.transpose(Image.ROTATE_180), 270: im.transpose(Image.ROTATE_90)}
    errors = {deg: float(np.abs(_probe(cand) - ref).mean()) for deg, cand in candidates.items()}
    best = min(errors, key=errors.get)
    if best == 0 or errors[best] > errors[0] * MARGIN:
        return im, 0
    return candidates[best], best


def _thumbnail(item: dict, storage: Path) -> Path | None:
    key = item.get("thumb_key")
    if not key:
        return None
    path = storage / key
    return path if path.exists() else None


def prepare(manifest: Path, out: Path, storage: Path | None = None) -> dict:
    manifest = Path(manifest)
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    items = doc.get("items", [])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # Thumbnails sit beside the manifest's folder, under data/storage. The two machines
    # keep the project at different paths, so the manifest stores the relative key only.
    storage = Path(storage) if storage else manifest.resolve().parent.parent / "storage"

    made: list[str] = []
    re_encoded: list[str] = []
    rotated: list[str] = []
    unverified: list[str] = []
    check: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []

    for item in items:
        raw = item.get("source_path")
        if not raw:
            missing.append(f"{item.get('n')}: no source path recorded")
            continue
        src = Path(raw)
        if not src.exists():
            missing.append(str(src))
            continue
        if imaging.is_video(src):
            # A still carousel: a video needs picking a frame or uploading on its own.
            skipped.append(str(src))
            continue

        dst = out / f"{int(item['n']):02d}_{src.stem}.jpg"
        thumb_path = _thumbnail(item, storage)
        if thumb_path is None:
            unverified.append(dst.name)
            if src.suffix.lower() in LOSSLESS and _upright(src):
                exif.strip_location(src, dst)
            else:
                imaging.load(src).save(dst, "JPEG", quality=QUALITY, optimize=True)
                re_encoded.append(dst.name)
            made.append(dst.name)
            continue

        im = imaging.load(src)
        with Image.open(thumb_path) as thumb:
            im, turn = _match_thumbnail(im, thumb)
            tall = thumb.height > thumb.width
        if turn:
            rotated.append(f"{dst.name} ({turn} cw)")
        if not turn and src.suffix.lower() in LOSSLESS and _upright(src):
            exif.strip_location(src, dst)
        else:
            im.save(dst, "JPEG", quality=QUALITY, optimize=True)
            re_encoded.append(dst.name)
        if (im.height > im.width) != tall:
            check.append(dst.name)
        made.append(dst.name)

    return {
        "out": str(out),
        "written": len(made),
        # HEIC, and any JPEG that had to be rotated
        "re_encoded": len(re_encoded),
        "files": made,
        # turned to match the thumbnail, because the original did not say which way up it goes
        "rotated": rotated,
        # no thumbnail to check against, so the file's own Orientation tag was trusted
        "unverified": unverified,
        # still disagrees with the thumbnail after all of the above: look at these
        "check": check,
        "missing": missing,
        "skipped_video": skipped,
    }
