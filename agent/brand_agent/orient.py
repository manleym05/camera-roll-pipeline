"""Turn photos upright when the camera didn't record how it was held.

Cheap cameras (the "105B", Kodak PIXPRO, Fuji XP50) write Orientation=1 even for vertical shots, so
exif_transpose leaves them sideways. A linear head on CLIP embeddings predicts how many quarter-turns
clockwise an image is from upright; it was trained on the owner's iPhone thumbnails (whose orientation is
reliable) rotated four ways — see scripts/train_orientation.py. Scoring all four rotations of the
photo and summing makes it robust: 97% upright accuracy on held-out iPhone photos. We only rotate
when the best rotation beats "as is" by MARGIN, and never touch iPhone photos or tagged files.
"""

from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image

from brand_agent.exif import Exif

HEAD = Path(__file__).with_name("orient_head.npz")
# Log-likelihood gap needed to rotate. On 250 hand-checked camera photos, every rotation above 5
# was right; the misses below it were near-ties.
MARGIN = 5.0


def needs_check(exif: Exif) -> bool:
    if exif.orientation not in (None, 1):
        return False  # the camera recorded it; exif_transpose already applied it
    return not (exif.make or "").lower().startswith("apple")


def best_turn(logp: np.ndarray, margin: float = MARGIN) -> int:
    """logp[k] = log P(label | image turned k more quarter-turns clockwise). Returns h: the image is h
    quarter-turns clockwise from upright (0 when not confident)."""
    scores = np.array([sum(logp[k][(h + k) % 4] for k in range(4)) for h in range(4)])
    h = int(scores.argmax())
    return h if scores[h] - scores[0] >= margin else 0


@cache
def _head() -> tuple[np.ndarray, np.ndarray]:
    d = np.load(HEAD)
    return d["W"], d["b"]


def upright(clip, im: Image.Image) -> Image.Image:
    small = im.copy()
    small.thumbnail((768, 768))
    emb = clip.embed([small.rotate(-90 * k, expand=True) for k in range(4)])
    W, b = _head()
    z = emb * 10 @ W.T + b
    logp = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
    h = best_turn(logp)
    return im.rotate(90 * h, expand=True) if h else im
