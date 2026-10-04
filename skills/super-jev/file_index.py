"""Per-principal file index (stdlib sqlite3, no FTS5).

One rule: a file's bytes are hashed only when its stat (size, mtime_ns) changes, or at serve time (not here).
Only a file with a passing review whose current sha equals the reviewed sha, and with no secret-shaped text,
gets a `files` row. Held, secret, edited or unreviewed bytes never enter the index. The updater walks stats only;
it is not wired into ask.py yet.

    idx = FileIndex(principal)              # <state dir>/<principal>/index.sqlite
    idx.update(pointer, roots=[folder])     # seeded from the prepare-cache on first run
    idx.purge(pointer)                      # unshare / refresh: DELETE every row of that pointer
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, pointer TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    sha256 TEXT, pass INTEGER, reviewed_sha TEXT);
CREATE TABLE IF NOT EXISTS toc(path TEXT PRIMARY KEY, sha TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS pointers(pointer TEXT PRIMARY KEY, stale INTEGER DEFAULT 0, roots TEXT, checked_at REAL);
-- stat memo for files seen but never ingested (held, edited, new, unreviewed): so they are not re-hashed
CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY, pointer TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    sha256 TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS files_pointer ON files(pointer);
CREATE INDEX IF NOT EXISTS seen_pointer ON seen(pointer);
"""


def _has_secret(text: str) -> bool:
    from prepare_bulk import has_secret
    return has_secret(text)


def _state_dir(principal: str) -> Path:
    root = os.environ.get("SUPERJEV_STATE_DIR")
    base = Path(root).expanduser() if root else Path.home() / ".local/state/super-jev"
    return base / principal


class FileIndex:
    def __init__(self, principal: str, db_path=None):
        self.principal = principal
        self.path = Path(db_path) if db_path else _state_dir(principal) / "index.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def _walk(self, roots):
        for root in roots or []:
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    yield os.path.join(dirpath, n)

    def _drop(self, path):
        for t in ("files", "toc", "seen"):
            self.db.execute(f"DELETE FROM {t} WHERE path=?", (path,))

    def update(self, pointer: str, entries: dict = None, roots=None) -> dict:
        """Stat-diff a pointer's files; read and sha ONLY files whose stat changed (or never seen).
        `entries` is the reviewed prepare-cache of the pointer (path -> record); default: load it from the
        prepare-cache. `roots` are the connected folders, walked by stat only to find new files.
        Returns {"hashed": n, "changed": [...], "new": [...], "gone": [...], "stale": bool}."""
        if entries is None:
            from prepare_bulk import load_cache_files
            entries = load_cache_files(pointer)
        known = {r[0]: r[1:] for r in self.db.execute("SELECT path,size,mtime_ns FROM files WHERE pointer=?", (pointer,))}
        known.update({r[0]: r[1:] for r in self.db.execute("SELECT path,size,mtime_ns FROM seen WHERE pointer=?", (pointer,))})
        out = {"hashed": 0, "changed": [], "new": [], "gone": [], "stale": False}
        row = self.db.execute("SELECT roots FROM pointers WHERE pointer=?", (pointer,)).fetchone()
        if roots is None and row and row[0]:
            roots = json.loads(row[0])
        paths = set(entries) | set(known)
        paths |= {p for p in self._walk(roots)}
        for p in sorted(paths):
            ent = entries.get(p)
            try:
                st = os.stat(p)
            except OSError:
                if p in known:
                    self._drop(p); out["gone"].append(p)
                continue
            if not os.path.isfile(p):
                continue
            if ent is None and p not in known and roots is None:
                continue
            if known.get(p) == (st.st_size, st.st_mtime_ns):
                continue
            if ent is not None and not ent.get("pass"):
                continue  # not reviewed as passing: never read, never indexed
            with open(p, "rb") as f:
                raw = f.read()
            out["hashed"] += 1
            sha = hashlib.sha256(raw).hexdigest()
            was_known = p in known
            reviewed = (ent or {}).get("sha256")
            self._drop(p)
            reason = None
            if ent is None:
                reason = "new"
            elif sha != reviewed:
                reason = "edited"
            elif _has_secret(raw.decode("utf-8", "replace")):
                reason = "held"
            if reason:
                self.db.execute("INSERT INTO seen VALUES(?,?,?,?,?,?)", (p, pointer, st.st_size, st.st_mtime_ns, sha, reason))
                (out["new"] if reason == "new" else out["changed"]).append(p)
            else:
                self.db.execute("INSERT INTO files VALUES(?,?,?,?,?,1,?)", (p, pointer, st.st_size, st.st_mtime_ns, sha, reviewed))
                toc = {k: ent.get(k) for k in ("description", "question", "kind", "status", "as_of", "subject")}
                self.db.execute("INSERT INTO toc VALUES(?,?,?)", (p, sha, json.dumps(toc)))
                if was_known:
                    out["changed"].append(p)
        first = row is None  # the seeding pass is not a change
        out["stale"] = bool(out["changed"] or out["new"] or out["gone"]) and not first
        stale = 1 if out["stale"] else (self.db.execute("SELECT stale FROM pointers WHERE pointer=?", (pointer,)).fetchone() or (0,))[0]
        self.db.execute("INSERT OR REPLACE INTO pointers VALUES(?,?,?,strftime('%s','now'))",
                        (pointer, stale, json.dumps(list(roots)) if roots else None))
        self.db.commit()
        return out

    def is_stale(self, pointer: str) -> bool:
        r = self.db.execute("SELECT stale FROM pointers WHERE pointer=?", (pointer,)).fetchone()
        return bool(r and r[0])

    def count(self, path: str = None, pointer: str = None) -> int:
        q, a = "SELECT COUNT(*) FROM files", []
        if path is not None:
            q += " WHERE path=?"; a = [path]
        elif pointer is not None:
            q += " WHERE pointer=?"; a = [pointer]
        return self.db.execute(q, a).fetchone()[0]

    def purge(self, pointer: str) -> None:
        """Unshare / refresh: DELETE every row of the pointer (files, toc, seen, pointers)."""
        self.db.execute("DELETE FROM toc WHERE path IN (SELECT path FROM files WHERE pointer=?)", (pointer,))
        for t in ("files", "seen", "pointers"):
            self.db.execute(f"DELETE FROM {t} WHERE pointer=?", (pointer,))
        self.db.commit()
