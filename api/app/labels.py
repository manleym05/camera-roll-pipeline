"""Import calendar entries used to name events.

- Google Calendar: the calendar's secret ICS address (Settings → Integrate calendar), or an .ics export.
- Paper calendar: CSV transcribed from photos, columns: start_date,end_date,title,location
"""

import csv
import re
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import EventLabel

LOCAL_TZ = ZoneInfo("America/New_York")

# Life HQ - Daily Plan is auto-filled with sleep, meals and health logs — not events.
DEFAULT_IGNORE = re.compile(r"^\s*(😴|🍳|🥗|🍽|🥩|🍎|🥤|☕|💊|⚖)|\b(sleep|breakfast|lunch|dinner|snack|brunch)\b", re.I)


def _as_dt(value: date | datetime, end: bool = False) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=LOCAL_TZ)
    return datetime.combine(value, time.max if end else time.min, LOCAL_TZ)


def _upsert(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = insert(EventLabel).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_={c: stmt.excluded[c] for c in ("title", "start_at", "end_at", "location", "raw")},
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def import_ics(session: Session, source: str, since: date, until: date, ignore=DEFAULT_IGNORE) -> int:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as resp:
            data = resp.read()
    else:
        data = Path(source).read_bytes()
    cal = icalendar.Calendar.from_ical(data)
    rows = []
    for ev in recurring_ical_events.of(cal).between(since, until):
        title = str(ev.get("SUMMARY", "")).strip()
        if not title or ignore.search(title):
            continue
        start = ev.decoded("DTSTART")
        end = ev.decoded("DTEND") if ev.get("DTEND") else start
        if isinstance(end, date) and not isinstance(end, datetime):
            end = end - timedelta(days=1)  # ICS all-day end dates are exclusive
        rows.append(
            {
                "source": "calendar",
                "external_id": f"{ev.get('UID')}@{start}",
                "title": title,
                "start_at": _as_dt(start),
                "end_at": _as_dt(end, end=True),
                "location": str(ev.get("LOCATION", "")) or None,
                "raw": {"description": str(ev.get("DESCRIPTION", ""))[:2000]},
            }
        )
    return _upsert(session, rows)


def import_paper_csv(session: Session, path: str) -> int:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, rec in enumerate(csv.DictReader(f)):
            start = date.fromisoformat(rec["start_date"].strip())
            end = date.fromisoformat((rec.get("end_date") or rec["start_date"]).strip())
            rows.append(
                {
                    "source": "paper",
                    "external_id": f"{Path(path).name}:{i}",
                    "title": rec["title"].strip(),
                    "start_at": _as_dt(start),
                    "end_at": _as_dt(end, end=True),
                    "location": (rec.get("location") or "").strip() or None,
                    "raw": rec,
                }
            )
    return _upsert(session, rows)
