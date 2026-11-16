"""Admin commands.

    uv run python -m app.cli events rebuild
    uv run python -m app.cli events list
    uv run python -m app.cli labels ics <url-or-file> [--since 2024-01-01]
    uv run python -m app.cli labels paper <file.csv>
    uv run python -m app.cli score shortlist        # ~30 candidates per event
    uv run python -m app.cli score export [--all]   # packets for the scorer in data/scoring/
    uv run python -m app.cli score import [files]   # validate + store <event_id>.result.json
    uv run python -m app.cli score show <n>         # ranked candidates for event #n of `events list`
    uv run python -m app.cli captions export [--all]  # packets for the caption writer in data/captions/
    uv run python -m app.cli captions import [files]  # validate + store <event_id>.result.json
    uv run python -m app.cli media shift --event <id> --days -34 --hours 5 [--prefix DSCF] [--dry-run]
    uv run python -m app.cli collection export <id>  # manifest of originals for brand-agent prepare
"""

import argparse
import json
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app import captions, events, labels, scoring, shortlist
from app.config import settings
from app.db import SessionLocal
from app.config import REPO_ROOT
from app.models import Candidate, Event, Media, Post


def print_events() -> None:
    with SessionLocal() as s:
        rows = s.scalars(select(Event).order_by(Event.start_at)).all()
    if not rows:
        print("No events yet. Ingest media, then run: events rebuild")
        return
    primary = settings.primary_owner
    print(f"{'#':>3}  {'kind':<5}  {'start':<10}  {'end':<10}  {'days':>4}  {'media':>5}  {'owners':<16}  {'src':<8}  label")
    for i, e in enumerate(rows, 1):
        days = (e.end_at.date() - e.start_at.date()).days + 1
        owners = " ".join(f"{'you' if o == primary else o[:3]}:{n}" for o, n in sorted(e.owners.items(), key=lambda kv: kv[0] != primary))
        flag = "" if primary in e.owners else "  [none of your photos - were you there?]"
        flag += "" if e.status == "pending" else f"  [{e.status}]"
        print(
            f"{i:>3}  {e.kind:<5}  {e.start_at:%Y-%m-%d}  {e.end_at:%Y-%m-%d}  {days:>4}  "
            f"{e.media_count:>5}  {owners:<16}  {e.label_source:<8}  {e.label}{flag}"
        )
    trips = sum(e.kind == "trip" for e in rows)
    solo = sum(primary not in e.owners for e in rows)
    print(f"\n{len(rows)} events ({trips} trips, {len(rows) - trips} local; {solo} without your photos)")


def show_event(n: int) -> None:
    with SessionLocal() as s:
        ev = s.scalars(select(Event).order_by(Event.start_at).offset(n - 1).limit(1)).first()
        if ev is None:
            print(f"no event #{n}")
            return
        rows = s.execute(
            select(Candidate, Media.thumb_key, Media.owner)
            .join(Media, Media.id == Candidate.media_id)
            .where(Candidate.event_id == ev.id)
            .order_by(Candidate.manual_rank.nulls_last(), Candidate.rank.nulls_last())
        ).all()
    print(f"#{n} {ev.label}  ({ev.start_at:%Y-%m-%d} to {ev.end_at:%Y-%m-%d}, {ev.label_source}, {ev.status})")
    if ev.summary:
        print(f"    {ev.summary}")
    for c, thumb, owner in rows:
        sub = c.subscores or {}
        flag = f"  NEVER POST: {c.never_post_reason}" if c.never_post else ""
        credit = f"  [photo: {c.credit}]" if c.credit else ""
        print(f"{c.rank or '-':>3}  {c.score if c.score is not None else '-':>5}  {sub.get('pillar', ''):<8} "
              f"{thumb.split('/')[-1][:12]}  {c.rationale or ''}{credit}{flag}")


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="group", required=True)

    ev = sub.add_parser("events").add_subparsers(dest="cmd", required=True)
    ev.add_parser("rebuild")
    ev.add_parser("list")

    lb = sub.add_parser("labels").add_subparsers(dest="cmd", required=True)
    ics = lb.add_parser("ics")
    ics.add_argument("source")
    ics.add_argument("--since", type=date.fromisoformat, default=date(2024, 1, 1))
    ics.add_argument("--until", type=date.fromisoformat, default=date.today())
    paper = lb.add_parser("paper")
    paper.add_argument("path")

    sc = sub.add_parser("score").add_subparsers(dest="cmd", required=True)
    sc.add_parser("shortlist")
    exp = sc.add_parser("export")
    exp.add_argument("--all", action="store_true", help="include events that are already scored")
    imp = sc.add_parser("import")
    imp.add_argument("files", nargs="*")
    show = sc.add_parser("show")
    show.add_argument("n", type=int)

    cp = sub.add_parser("captions").add_subparsers(dest="cmd", required=True)
    cexp = cp.add_parser("export")
    cexp.add_argument("--all", action="store_true", help="include events that already have drafts")
    cimp = cp.add_parser("import")
    cimp.add_argument("files", nargs="*")

    cl = sub.add_parser("collection").add_subparsers(dest="cmd", required=True)
    cex = cl.add_parser("export", help="list a collection's originals, for brand-agent prepare")
    cex.add_argument("id")
    cex.add_argument("--out", default=None, help="manifest path (default data/export/<id>.json)")

    md = sub.add_parser("media").add_subparsers(dest="cmd", required=True)
    sh = md.add_parser("shift", help="move an event's media in time (a camera with a wrong clock)")
    sh.add_argument("--event", action="append", required=True, metavar="ID", help="event id, repeatable")
    sh.add_argument("--days", type=float, default=0.0)
    sh.add_argument("--hours", type=float, default=0.0)
    sh.add_argument("--prefix", default=None, help="only files whose name starts with this, e.g. DSCF")
    sh.add_argument("--dry-run", action="store_true")

    args = p.parse_args()
    if args.group == "events" and args.cmd == "rebuild":
        with SessionLocal() as s:
            print(events.rebuild(s))
        print_events()
    elif args.group == "events":
        print_events()
    elif args.group == "score":
        with SessionLocal() as s:
            if args.cmd == "shortlist":
                print(shortlist.shortlist(s))
            elif args.cmd == "export":
                paths = scoring.export_packets(s, only_unscored=not args.all)
                print(f"wrote {len(paths)} packets to {scoring.SCORING_DIR}")
            elif args.cmd == "import":
                from pathlib import Path

                for row in scoring.import_results(s, [Path(f) for f in args.files] or None):
                    print(row)
        if args.cmd == "show":
            show_event(args.n)
    elif args.group == "captions":
        with SessionLocal() as s:
            if args.cmd == "export":
                paths = captions.export_packets(s, only_missing=not args.all)
                print(f"wrote {len(paths)} packets to {captions.CAPTIONS_DIR}")
            else:
                from pathlib import Path

                for row in captions.import_results(s, [Path(f) for f in args.files] or None):
                    print(row)
    elif args.group == "collection":
        from pathlib import Path

        pid = uuid.UUID(args.id)
        with SessionLocal() as s:
            post = s.get(Post, pid)
            if post is None or post.event_id is not None:
                raise SystemExit(f"no collection {args.id} (collections have no event)")
            ids = [uuid.UUID(str(i)) for i in (post.media_ids or [])]
            media = {m.id: m for m in s.scalars(select(Media).where(Media.id.in_(ids)))}
            items = []
            for n, mid in enumerate(ids, 1):
                m = media.get(mid)
                if m is None:
                    continue
                # thumb_key is relative, not a full path: prepare runs on the desktop,
                # which keeps the project somewhere else, and it is the only record of
                # which way up a photo goes when the original carries no Orientation tag.
                items.append({"n": n, "media_id": str(mid), "source_path": m.source_path,
                              "thumb_key": m.thumb_key, "owner": m.owner, "kind": m.kind})
            doc = {"collection": str(pid), "platform": post.platform, "status": post.status,
                   "caption": post.caption, "items": items}
        out = Path(args.out) if args.out else REPO_ROOT / "data" / "export" / f"{pid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print(f"wrote {len(items)} of {len(ids)} items to {out}")
        missing = [i["n"] for i in items if not i["source_path"]]
        if missing:
            print(f"no source path recorded for: {missing}")
        print("originals live on the desktop. There, run:")
        print(f"  uv run --directory agent brand-agent prepare --manifest {out}")

    elif args.group == "media":
        delta = timedelta(days=args.days, hours=args.hours)
        if not delta:
            raise SystemExit("nothing to do: pass --days and/or --hours")
        with SessionLocal() as s:
            out = events.shift_media(s, [uuid.UUID(e) for e in args.event], delta, args.prefix)
            if args.dry_run:
                s.rollback()
            else:
                s.commit()
            print({**out, "dry_run": args.dry_run})
        if not args.dry_run:
            print("media moved. now run: events rebuild")
    elif args.cmd == "ics":
        with SessionLocal() as s:
            print(f"imported {labels.import_ics(s, args.source, args.since, args.until)} calendar labels")
    else:
        with SessionLocal() as s:
            print(f"imported {labels.import_paper_csv(s, args.path)} paper labels")


if __name__ == "__main__":
    main()
