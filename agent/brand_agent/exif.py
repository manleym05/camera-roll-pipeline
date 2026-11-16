"""Read capture metadata with exiftool (handles HEIC, HEVC and MOV from iPhone)."""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FALLBACK_TZ = ZoneInfo("America/New_York")
DATE_FMT = "%Y-%m-%dT%H:%M:%S%z"

TAGS = [
    "-SubSecDateTimeOriginal",
    "-DateTimeOriginal",
    "-CreationDate",
    "-CreateDate",
    "-GPSLatitude#",
    "-GPSLongitude#",
    "-Make",
    "-Model",
    "-ImageWidth",
    "-ImageHeight",
    "-Duration#",
    "-ContentIdentifier",
    "-FileType",
    "-Software",
    "-Orientation#",
]


@dataclass
class Exif:
    path: Path
    file_type: str
    taken_at: datetime | None
    lat: float | None
    lon: float | None
    make: str | None
    model: str | None
    width: int | None
    height: int | None
    duration_s: float | None
    content_id: str | None
    software: str | None
    orientation: int | None = None  # EXIF 1-8; None when the camera didn't record it


def _exiftool() -> str:
    exe = shutil.which("exiftool")
    if exe:
        return exe
    winget = Path.home() / "AppData/Local/Programs/ExifTool/ExifTool.exe"
    if winget.exists():
        return str(winget)
    raise RuntimeError("exiftool not found — install with: winget install OliverBetz.ExifTool")


def _parse_date(value) -> datetime | None:
    # exiftool emits DATE_FMT, with an empty %z when the file carries no UTC offset.
    if not value or not isinstance(value, str) or value.startswith("0000"):
        return None
    for fmt in (DATE_FMT, "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=FALLBACK_TZ)
        except ValueError:
            continue
    return None


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_many(paths: list[Path], chunk: int = 200) -> list[Exif]:
    out: list[Exif] = []
    exe = _exiftool()
    for i in range(0, len(paths), chunk):
        batch = paths[i : i + chunk]
        with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as f:
            f.write("\n".join(str(p) for p in batch))
            argfile = f.name
        try:
            proc = subprocess.run(
                [exe, "-j", "-charset", "filename=utf8", "-d", DATE_FMT, "-api", "QuickTimeUTC", *TAGS, "-@", argfile],
                capture_output=True,
                check=False,
            )
        finally:
            Path(argfile).unlink(missing_ok=True)
        for rec in json.loads(proc.stdout.decode("utf-8") or "[]"):
            taken = None
            for key in ("SubSecDateTimeOriginal", "DateTimeOriginal", "CreationDate", "CreateDate"):
                taken = _parse_date(rec.get(key))
                if taken:
                    break
            lat, lon = _num(rec.get("GPSLatitude")), _num(rec.get("GPSLongitude"))
            if lat == 0 and lon == 0:
                lat = lon = None
            out.append(
                Exif(
                    path=Path(rec["SourceFile"]),
                    file_type=str(rec.get("FileType", "")).upper(),
                    taken_at=taken,
                    lat=lat,
                    lon=lon,
                    make=rec.get("Make"),
                    model=rec.get("Model"),
                    width=rec.get("ImageWidth"),
                    height=rec.get("ImageHeight"),
                    duration_s=_num(rec.get("Duration")),
                    content_id=rec.get("ContentIdentifier"),
                    software=rec.get("Software"),
                    orientation=rec.get("Orientation"),
                )
            )
    return out


def strip_location(src: Path, dst: Path) -> Path:
    """Write a copy of src with GPS and other location metadata removed (for full-size uploads)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    subprocess.run(
        [_exiftool(), "-gps:all=", "-xmp:geotag=", "-xmp-exif:all=", "-quicktime:gps*=", "-location:all=", "-o", str(dst), str(src)],
        capture_output=True,
        check=True,
    )
    return dst
