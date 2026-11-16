import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from brand_agent import orient
from brand_agent.exif import Exif, _exiftool, read_many


def _exif(make=None, orientation=None):
    return Exif(Path("x.jpg"), "JPEG", None, None, None, make, "105B", 3264, 2448, None, None, None, orientation)


def _logp(true_turn: int, confidence: float) -> np.ndarray:
    """Rows k: the photo turned k more quarter-turns; the label it shows is (true_turn + k) % 4."""
    rows = np.full((4, 4), np.log((1 - confidence) / 3))
    for k in range(4):
        rows[k][(true_turn + k) % 4] = np.log(confidence)
    return rows


def test_best_turn_recovers_rotation():
    assert orient.best_turn(_logp(1, 0.9)) == 1
    assert orient.best_turn(_logp(3, 0.9)) == 3
    assert orient.best_turn(_logp(0, 0.9)) == 0


def test_best_turn_leaves_near_ties_alone():
    logp = np.log(np.full((4, 4), 0.25))
    logp[0][1] = np.log(0.3)  # one weak vote for "sideways"
    assert orient.best_turn(logp) == 0


def test_only_untagged_non_iphone_photos_are_checked():
    assert orient.needs_check(_exif(make=None, orientation=1))
    assert orient.needs_check(_exif(make="FUJIFILM", orientation=None))
    assert not orient.needs_check(_exif(make="Apple", orientation=1))
    assert not orient.needs_check(_exif(make="Canon", orientation=6))


def test_shipped_head_has_expected_shape():
    W, b = orient._head()
    assert W.shape == (4, 512) and b.shape == (4,)


def test_orientation_tag_is_read(tmp_path):
    p = tmp_path / "rot.jpg"
    Image.new("RGB", (40, 30), "blue").save(p)
    subprocess.run([_exiftool(), "-overwrite_original", "-Orientation#=6", str(p)], check=True, capture_output=True)
    (e,) = read_many([p])
    assert e.orientation == 6
