import subprocess
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from brand_agent import dedupe
from brand_agent.exif import _exiftool, read_many, strip_location
from brand_agent.config import settings
from brand_agent.pipeline import _excluded

T0 = datetime(2025, 5, 10, 9, tzinfo=timezone.utc)


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_burst_collapses_to_sharpest():
    a = _unit([1, 0, 0])
    items = [
        dedupe.Item("a" * 64, T0, a, 10.0),
        dedupe.Item("b" * 64, T0 + timedelta(seconds=2), _unit([1, 0.05, 0]), 50.0),
        dedupe.Item("c" * 64, T0 + timedelta(seconds=4), _unit([0, 1, 0]), 5.0),  # different shot
        dedupe.Item("d" * 64, T0 + timedelta(minutes=10), a, 5.0),  # same look, much later
    ]
    g = dedupe.group(items)
    assert g["a" * 64][0] == g["b" * 64][0]
    assert g["b" * 64][1] and not g["a" * 64][1]
    assert g["c" * 64][1] and g["d" * 64][1]


def test_strip_location_removes_gps(tmp_path):
    src = tmp_path / "in.jpg"
    Image.new("RGB", (64, 64), "red").save(src)
    subprocess.run(
        [_exiftool(), "-overwrite_original", "-q", "-GPSLatitude=38.7", "-GPSLatitudeRef=N",
         "-GPSLongitude=9.1", "-GPSLongitudeRef=W", "-DateTimeOriginal=2025:05:10 09:00:00", str(src)],
        check=True,
    )
    assert read_many([src])[0].lat is not None
    out = read_many([strip_location(src, tmp_path / "out.jpg")])[0]
    assert out.lat is None and out.lon is None
    assert out.taken_at is not None  # capture time survives


def test_excluded_rules(monkeypatch):
    class E:
        file_type, make = "PNG", None

    class Cam:
        file_type, make = "HEIC", "Apple"

    assert _excluded(E, 0.1) == "screenshot"
    assert _excluded(Cam, 0.99) is None  # document filter off by default
    monkeypatch.setattr(settings, "doc_threshold", 0.9)
    assert _excluded(Cam, 0.99) == "document"
    assert _excluded(Cam, 0.5) is None
