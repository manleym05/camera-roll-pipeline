"""PC agent.

    uv run brand-agent scan                 # ingest PHOTOS_DIR
    uv run brand-agent scan --dir ..\\sample --limit 200
    uv run brand-agent scan --dry-run       # analyze only; nothing uploaded or sent
    uv run brand-agent scan --owner partner # ingest OWNER_DIRS["partner"]
    uv run brand-agent check --owner partner --expected 612   # verify an export first
    uv run brand-agent scan --rescan --model 105B --no-rebuild  # reprocess one camera's files
    uv run brand-agent prepare --manifest ..\\data\\export\\<id>.json   # upload-ready JPEGs
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from brand_agent import pipeline
from brand_agent.config import settings


def main() -> None:
    p = argparse.ArgumentParser(prog="brand-agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--dir", type=Path, default=None)
    scan.add_argument("--owner", default="owner", help="whose camera roll this is")
    scan.add_argument("--limit", type=int, default=None)
    scan.add_argument("--dry-run", action="store_true")
    scan.add_argument("--rescan", action="store_true", help="ignore cache; re-analyze and re-send everything")
    scan.add_argument("--no-rebuild", action="store_true", help="skip event detection after ingest")
    scan.add_argument("--model", action="append", help="only files from this camera model (repeatable)")
    prep = sub.add_parser("prepare", help="turn a collection manifest into upload-ready JPEGs")
    prep.add_argument("--manifest", type=Path, required=True)
    prep.add_argument("--out", type=Path, default=None, help="folder for the JPEGs (default beside the manifest)")
    prep.add_argument("--storage", type=Path, default=None,
                      help="where thumbs/ lives, for checking which way up each photo goes (default data/storage)")
    chk = sub.add_parser("check", help="inspect an export's completeness and quality; sends nothing")
    chk.add_argument("--dir", type=Path, default=None)
    chk.add_argument("--owner", default="owner")
    chk.add_argument("--expected", type=int, default=None, help="how many items the export should hold")
    args = p.parse_args()

    if args.cmd == "prepare":
        from brand_agent.prepare import prepare

        out = args.out or args.manifest.parent / args.manifest.stem
        print(json.dumps(prepare(args.manifest, out, args.storage), indent=2))
        return

    if args.cmd == "check":
        from brand_agent.check import check

        folder = args.dir or (settings.photos_dir if args.owner == "owner" else settings.owner_dirs.get(args.owner))
        if folder is None or not Path(folder).exists():
            raise SystemExit(f"Folder not found: {folder}")
        print(json.dumps(check(Path(folder), args.expected), indent=2))
        return

    folder = args.dir or (settings.photos_dir if args.owner == "owner" else settings.owner_dirs.get(args.owner))
    if folder is None:
        raise SystemExit(f"No folder for owner {args.owner!r}. Pass --dir or add it to OWNER_DIRS in .env.")
    if not folder.exists():
        raise SystemExit(f"Photos folder not found: {folder}\nSet PHOTOS_DIR in .env or pass --dir.")
    report = pipeline.run(folder, limit=args.limit, dry_run=args.dry_run, rebuild=not args.no_rebuild, rescan=args.rescan, owner=args.owner, models=args.model)
    out = asdict(report)
    out["failed"] = out["failed"][:20]
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
