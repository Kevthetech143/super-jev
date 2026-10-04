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
CREATE TABLE IF NOT EXISTS pointers(pointer TEXT PRIMARY KEY, stale INTEGER DEFAULT 0, roots TEXT, checked_at REAL,
    status TEXT, generation TEXT, views TEXT, listed INTEGER DEFAULT 0);
-- stat memo for files seen but never ingested (held, edited, new, unreviewed): so they are not re-hashed
CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY, pointer TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    sha256 TEXT, reason TEXT, reviewed_sha TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
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
        have = {r[1] for r in self.db.execute("PRAGMA table_info(pointers)")}
        for col, typ in (("status", "TEXT"), ("generation", "TEXT"), ("views", "TEXT"), ("listed", "INTEGER DEFAULT 0")):
            if col not in have:  # an index made by the first version: add the read-path columns
                self.db.execute(f"ALTER TABLE pointers ADD COLUMN {col} {typ}")

    def close(self):
        self.db.close()

    def _walk(self, roots):
        """New-file discovery by stat only, with the same guards connect's inventory() applies (credential
        suffix, logins / -secret / .bak names, vault and hidden folders, hidden files, links leaving the root,
        extension, size ceiling). No file body is opened."""
        import prepare_bulk as pb
        for root in roots or []:
            root = Path(root)
            base = root.resolve()
            files, linked = pb.walk_md(root)
            bases = [base] + [t for t in linked if not pb.SKIP_PARTS.intersection(x.casefold() for x in t.parts)]
            for p in files:
                rp = p.resolve()
                if pb.credential_suffix(p.name) or pb.credential_suffix(rp.name):
                    continue
                if not any(rp.is_relative_to(b) for b in bases):
                    continue
                if any(".bak" in n or Path(n).stem == "logins" or Path(n).stem.endswith("-secret")
                       for n in (p.name.casefold(), rp.name.casefold())):
                    continue
                b = next(b for b in bases if rp.is_relative_to(b))
                if pb.skipped_folder(p.relative_to(root).parts[:-1] + rp.relative_to(b).parts[:-1]):
                    continue
                if p.name.startswith(".") or rp.name.startswith("."):
                    continue
                if pb.path_has_secret(p.name) or pb.path_has_secret(rp.name):
                    continue
                try:
                    if rp.stat().st_size > pb.CEILING_BYTES:
                        continue
                except OSError:
                    continue
                yield str(p)

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
        known = {r[0]: r[1:] for r in self.db.execute("SELECT path,size,mtime_ns,reviewed_sha FROM files WHERE pointer=?", (pointer,))}
        known.update({r[0]: r[1:] for r in self.db.execute("SELECT path,size,mtime_ns,reviewed_sha FROM seen WHERE pointer=?", (pointer,))})
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
            if ent is None:
                if p in known and known[p][:2] == (st.st_size, st.st_mtime_ns):
                    continue
                if p not in known and roots is None:
                    continue
                # no passing review: never read. Record it from stat alone.
                was_known = p in known
                self._drop(p)
                self.db.execute("INSERT INTO seen VALUES(?,?,?,?,NULL,'new',NULL)", (p, pointer, st.st_size, st.st_mtime_ns))
                if not was_known:
                    out["new"].append(p)
                else:
                    out["changed"].append(p)
                continue
            if not ent.get("pass"):
                if p in known:
                    self._drop(p)  # review no longer passes: its rows go
                continue  # not reviewed as passing: never read, never indexed
            reviewed = ent.get("sha256")
            # unchanged stat AND the same review as when it was last judged: nothing to do. A changed or
            # newly appeared review (promotion) re-evaluates, so a seen file can become indexed.
            if p in known and known[p] == (st.st_size, st.st_mtime_ns, reviewed):
                continue
            with open(p, "rb") as f:
                raw = f.read()
            out["hashed"] += 1
            sha = hashlib.sha256(raw).hexdigest()
            was_known = p in known
            self._drop(p)
            reason = None
            if not reviewed or sha != reviewed:
                reason = "edited"
            elif _has_secret(raw.decode("utf-8", "replace")):
                reason = "held"
            if reason:
                self.db.execute("INSERT INTO seen VALUES(?,?,?,?,?,?,?)",
                                (p, pointer, st.st_size, st.st_mtime_ns, sha, reason, reviewed))
                out["changed"].append(p)
            else:
                self.db.execute("INSERT INTO files VALUES(?,?,?,?,?,1,?)", (p, pointer, st.st_size, st.st_mtime_ns, sha, reviewed))
                toc = {k: ent.get(k) for k in ("description", "question", "kind", "status", "as_of", "subject")}
                self.db.execute("INSERT INTO toc VALUES(?,?,?)", (p, sha, json.dumps(toc)))
                if was_known:
                    out["changed"].append(p)
        first = row is None  # the seeding pass is not a change
        out["stale"] = bool(out["changed"] or out["new"] or out["gone"]) and not first
        stale = 1 if out["stale"] else (self.db.execute("SELECT stale FROM pointers WHERE pointer=?", (pointer,)).fetchone() or (0,))[0]
        self.db.execute("INSERT INTO pointers(pointer,stale,roots,checked_at) VALUES(?,?,?,strftime('%s','now')) "
                        "ON CONFLICT(pointer) DO UPDATE SET stale=excluded.stale, roots=excluded.roots, checked_at=excluded.checked_at",
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

    # ---- read path (ask.py, flag-gated): no file is opened here ----

    def set_panel(self, rows: list) -> None:
        """Record the principal's visible pointers as the registry panel listed them (updater only):
        rows of {"pointer", "snapshotStatus"|"status", "generation", "viewOriginals"}. Pointers no longer listed go."""
        self.db.execute("UPDATE pointers SET listed=0")
        for r in rows:
            name = r.get("pointer")
            if not name:
                continue
            self.db.execute(
                "INSERT INTO pointers(pointer,stale,status,generation,views,listed,checked_at) VALUES(?,0,?,?,?,1,strftime('%s','now')) "
                "ON CONFLICT(pointer) DO UPDATE SET status=excluded.status, generation=excluded.generation, "
                "views=excluded.views, listed=1",
                (name, str(r.get("snapshotStatus") or r.get("status") or ""),
                 json.dumps(r.get("generation")), json.dumps(r.get("viewOriginals") or [])))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('synced_at', strftime('%s','now'))")
        self.db.commit()

    def synced_at(self):
        r = self.db.execute("SELECT v FROM meta WHERE k='synced_at'").fetchone()
        return float(r[0]) if r else None

    def generation_of(self, pointer: str):
        r = self.db.execute("SELECT generation FROM pointers WHERE pointer=?", (pointer,)).fetchone()
        return json.loads(r[0]) if r and r[0] else None

    def panel_rows(self) -> list:
        """The panel shape ask.py reads, from the pointers table. A pointer the updater saw changed on disk
        is reported refresh-required, like a stale registry row."""
        out = []
        for name, stale, status, gen, views in self.db.execute(
                "SELECT pointer,stale,status,generation,views FROM pointers WHERE listed=1 ORDER BY pointer"):
            row = {"pointer": name, "snapshotStatus": "refresh-required" if stale else (status or "ready")}
            if gen is not None and json.loads(gen) is not None:
                row["generation"] = json.loads(gen)
            v = json.loads(views) if views else []
            if v:
                row["viewOriginals"] = v
            out.append(row)
        return out

    def candidates(self, pointers) -> list:
        """[(pointer, path, entry)] of every indexed file of these pointers, entry shaped like a prepare-cache
        record (pass, sha256 = the reviewed sha, and the labels the TOC search reads)."""
        out = []
        for ptr in pointers:
            for path, sha, tj in self.db.execute(
                    "SELECT f.path,f.reviewed_sha,t.json FROM files f LEFT JOIN toc t ON t.path=f.path WHERE f.pointer=? ORDER BY f.path",
                    (ptr,)):
                out.append((ptr, path, {**(json.loads(tj) if tj else {}), "pass": True, "sha256": sha}))
        return out

    def mark_stale(self, pointer: str) -> None:
        self.db.execute("UPDATE pointers SET stale=1 WHERE pointer=?", (pointer,))
        self.db.commit()

    def purge(self, pointer: str) -> None:
        """Unshare / refresh: DELETE every row of the pointer (files, toc, seen, pointers)."""
        self.db.execute("DELETE FROM toc WHERE path IN (SELECT path FROM files WHERE pointer=?)", (pointer,))
        for t in ("files", "seen", "pointers"):
            self.db.execute(f"DELETE FROM {t} WHERE pointer=?", (pointer,))
        self.db.commit()
