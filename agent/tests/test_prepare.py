"""prepare(): upload-ready JPEGs, upright, with no location data."""

import json
import subprocess

import pytest
from PIL import Image, ImageDraw

from brand_agent.prepare import prepare


def _jpeg(path, size=(1600, 1200), orientation=None):
    Image.new("RGB", size, (60, 120, 190)).save(path, "JPEG", quality=95)
    if orientation:
        subprocess.run(["exiftool", "-overwrite_original", f"-Orientation={orientation}", "-n", str(path)],
                       capture_output=True, check=True)
    return path


def _manifest(tmp_path, *sources):
    m = tmp_path / "m.json"
    items = [{"n": i, "source_path": str(s)} for i, s in enumerate(sources, 1)]
    m.write_text(json.dumps({"items": items}), encoding="utf-8")
    return m


def test_rotated_jpeg_comes_out_upright(tmp_path):
    src = _jpeg(tmp_path / "SIDEWAYS.JPG", orientation=6)  # stored landscape, tagged portrait
    out = tmp_path / "out"
    report = prepare(_manifest(tmp_path, src), out)
    assert report["written"] == 1
    with Image.open(out / "01_SIDEWAYS.jpg") as im:
        assert im.size == (1200, 1600)          # rotation baked into the pixels
        assert im.getexif().get(274) in (None, 1)


def test_upright_jpeg_is_not_re_encoded(tmp_path):
    src = _jpeg(tmp_path / "FLAT.JPG")
    out = tmp_path / "out"
    report = prepare(_manifest(tmp_path, src), out)
    assert report["re_encoded"] == 0           # copied losslessly
    with Image.open(out / "01_FLAT.jpg") as im:
        assert im.size == (1600, 1200)


def test_gps_never_survives(tmp_path):
    src = _jpeg(tmp_path / "GEO.JPG")
    subprocess.run(["exiftool", "-overwrite_original", "-GPSLatitude=35.8341", "-GPSLongitude=-78.6273",
                    "-GPSLatitudeRef=N", "-GPSLongitudeRef=W", str(src)], capture_output=True, check=True)
    out = tmp_path / "out"
    prepare(_manifest(tmp_path, src), out)
    found = subprocess.run(["exiftool", "-s3", "-GPSLatitude", str(out / "01_GEO.jpg")],
                           capture_output=True, text=True).stdout.strip()
    assert found == ""


def test_numbering_follows_the_manifest(tmp_path):
    a = _jpeg(tmp_path / "AAA.JPG")
    b = _jpeg(tmp_path / "BBB.JPG")
    out = tmp_path / "out"
    prepare(_manifest(tmp_path, b, a), out)    # b first
    assert sorted(p.name for p in out.glob("*.jpg")) == ["01_BBB.jpg", "02_AAA.jpg"]


def test_missing_original_is_named_not_swallowed(tmp_path):
    out = tmp_path / "out"
    report = prepare(_manifest(tmp_path, tmp_path / "GONE.JPG"), out)
    assert report["written"] == 0
    assert "GONE.JPG" in report["missing"][0]


# --- which way up a photo goes -------------------------------------------------------
#
# A few originals are stored sideways and carry no Orientation tag to say so, so nothing
# in the file reveals it. Their thumbnails were corrected during review, and that is the
# only record. These cover prepare agreeing with the thumbnail.


def _scene(size=(1600, 1200)):
    """An asymmetric image, so one rotation of it is tellable from another."""
    im = Image.new("RGB", size, (30, 40, 60))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, size[0], size[1] // 4], fill=(235, 225, 120))
    d.ellipse([size[0] // 10, size[1] // 2, size[0] // 3, size[1] * 4 // 5], fill=(215, 60, 55))
    return im


def _thumb(path, im):
    t = im.copy()
    t.thumbnail((768, 768))
    t.save(path, "JPEG", quality=82)
    return path


def _manifest_with_thumbs(tmp_path, *pairs):
    m = tmp_path / "m.json"
    items = [{"n": i, "source_path": str(s), "thumb_key": k} for i, (s, k) in enumerate(pairs, 1)]
    m.write_text(json.dumps({"items": items}), encoding="utf-8")
    return m


def test_sideways_original_with_no_tag_is_turned_to_match_the_thumbnail(tmp_path):
    upright = _scene()                                  # 1600x1200, bright band on top
    storage = tmp_path / "storage"
    storage.mkdir()
    _thumb(storage / "t.jpg", upright)
    src = tmp_path / "DSCF0001.JPG"
    upright.transpose(Image.ROTATE_90).save(src, "JPEG", quality=95)   # stored on its side
    with Image.open(src) as im:
        assert im.size == (1200, 1600)
        assert im.getexif().get(274, 1) == 1            # and nothing in the file says so

    out = tmp_path / "out"
    report = prepare(_manifest_with_thumbs(tmp_path, (src, "t.jpg")), out, storage)

    assert report["rotated"] == ["01_DSCF0001.jpg (90 cw)"]
    assert report["check"] == []
    with Image.open(out / "01_DSCF0001.jpg") as im:
        assert im.size == (1600, 1200)                  # back the way the owner reviewed it


def test_a_photo_that_agrees_with_its_thumbnail_is_left_alone(tmp_path):
    upright = _scene()
    storage = tmp_path / "storage"
    storage.mkdir()
    _thumb(storage / "t.jpg", upright)
    src = tmp_path / "FINE.JPG"
    upright.save(src, "JPEG", quality=95)

    out = tmp_path / "out"
    report = prepare(_manifest_with_thumbs(tmp_path, (src, "t.jpg")), out, storage)

    assert report["rotated"] == []
    assert report["re_encoded"] == 0                    # still copied losslessly
    assert report["check"] == []


def test_a_photo_with_no_thumbnail_is_flagged_rather_than_guessed(tmp_path):
    src = _jpeg(tmp_path / "LONE.JPG")
    out = tmp_path / "out"
    report = prepare(_manifest(tmp_path, src), out, tmp_path / "storage")

    assert report["unverified"] == ["01_LONE.jpg"]
    assert report["rotated"] == []
    assert report["written"] == 1                       # still produced, just unchecked


def test_a_thumbnail_that_went_missing_does_not_stop_the_batch(tmp_path):
    src = _jpeg(tmp_path / "GHOST.JPG")
    storage = tmp_path / "storage"
    storage.mkdir()
    out = tmp_path / "out"
    report = prepare(_manifest_with_thumbs(tmp_path, (src, "gone.jpg")), out, storage)

    assert report["written"] == 1
    assert report["unverified"] == ["01_GHOST.jpg"]


def test_a_near_symmetric_photo_is_not_turned_on_a_coin_flip(tmp_path):
    """A flat photo scores every rotation about the same. Guessing would be worse."""
    flat = Image.new("RGB", (1200, 1200), (90, 110, 130))
    storage = tmp_path / "storage"
    storage.mkdir()
    _thumb(storage / "t.jpg", flat)
    src = tmp_path / "FLATSQ.JPG"
    flat.save(src, "JPEG", quality=95)

    out = tmp_path / "out"
    report = prepare(_manifest_with_thumbs(tmp_path, (src, "t.jpg")), out, storage)

    assert report["rotated"] == []
