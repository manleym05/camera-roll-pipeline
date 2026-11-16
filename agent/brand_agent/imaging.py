"""Decode stills and video frames, measure sharpness, count faces, write thumbnails."""

import io
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
THUMB_EDGE = 768

_faces = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXT


def load(path: Path, duration_s: float | None = None) -> Image.Image:
    """An upright RGB still: the image itself, or a frame from 1/3 into a video."""
    if is_video(path):
        at = max(0.0, (duration_s or 3.0) / 3)
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True,
            check=True,
        ).stdout
        return Image.open(io.BytesIO(raw)).convert("RGB")
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def _gray(im: Image.Image, edge: int = 1024) -> np.ndarray:
    small = im.copy()
    small.thumbnail((edge, edge))
    return cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)


def sharpness(im: Image.Image) -> float:
    return float(cv2.Laplacian(_gray(im), cv2.CV_64F).var())


def face_count(im: Image.Image) -> int:
    gray = _gray(im)
    min_side = max(32, min(gray.shape) // 20)
    found = _faces.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    return len(found)


def thumbnail_jpeg(im: Image.Image) -> bytes:
    """Re-encoded from pixels, so the thumbnail carries no EXIF/GPS."""
    thumb = im.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    buf = io.BytesIO()
    thumb.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()
