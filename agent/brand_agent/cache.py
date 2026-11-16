"""Local record of files already handled, so re-scans only touch new or changed files."""

import hashlib
import sqlite3
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INT, mtime REAL, sha256 TEXT, status TEXT)"
        )

    def lookup(self, path: Path) -> tuple[str, str] | None:
        """(sha256, status) if the file is unchanged since we last saw it."""
        st = path.stat()
        row = self.db.execute("SELECT size, mtime, sha256, status FROM files WHERE path = ?", (str(path),)).fetchone()
        if row and row[0] == st.st_size and row[1] == st.st_mtime:
            return row[2], row[3]
        return None

    def mark(self, path: Path, sha: str, status: str) -> None:
        st = path.stat()
        self.db.execute(
            "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)", (str(path), st.st_size, st.st_mtime, sha, status)
        )

    def commit(self) -> None:
        self.db.commit()
