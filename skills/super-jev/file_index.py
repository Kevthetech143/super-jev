"""Per-principal file index (stdlib sqlite3; FTS5 passages, contentless, for the flag-on ask).

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
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, pointer TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    sha256 TEXT, pass INTEGER, reviewed_sha TEXT);
CREATE TABLE IF NOT EXISTS toc(path TEXT PRIMARY KEY, sha TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS pointers(pointer TEXT PRIMARY KEY, stale INTEGER DEFAULT 0, roots TEXT, checked_at REAL,
    status TEXT, generation TEXT, views TEXT, listed INTEGER DEFAULT 0, entries INTEGER, complete INTEGER);
-- stat memo for files seen but never ingested (held, edited, new, unreviewed): so they are not re-hashed
CREATE TABLE IF NOT EXISTS seen(path TEXT PRIMARY KEY, pointer TEXT NOT NULL, size INTEGER, mtime_ns INTEGER,
    sha256 TEXT, reason TEXT, reviewed_sha TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS files_pointer ON files(pointer);
CREATE INDEX IF NOT EXISTS seen_pointer ON seen(pointer);
"""

# S3b: passages as a contentless FTS5 table plus the rows the ask re-scores from (all built by the updater only).
# Only files of the `files` table (reviewed, no secret-shaped text) are ever put here; held / edited ones never.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(body, toc, content='', contentless_delete=1);
CREATE TABLE IF NOT EXISTS fts_map(rid INTEGER PRIMARY KEY, path TEXT UNIQUE, pointer TEXT NOT NULL, person TEXT NOT NULL DEFAULT '',
    sha TEXT, lab TEXT, npass INTEGER, psize REAL, words TEXT);
-- the vocabulary (reference-counted by file) and its bigrams: the ask finds close spellings without scanning every word
CREATE TABLE IF NOT EXISTS fvocab(word TEXT PRIMARY KEY, n INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS vbigram(bg TEXT, word TEXT, PRIMARY KEY(bg, word)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS fts_map_pointer ON fts_map(pointer, person);
CREATE TABLE IF NOT EXISTS fts_stats(pointer TEXT, person TEXT, n INTEGER, passages INTEGER, psize REAL, PRIMARY KEY(pointer, person));
CREATE TABLE IF NOT EXISTS witems(path TEXT PRIMARY KEY, sha TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS tocpage(path TEXT PRIMARY KEY, sha TEXT, json TEXT);
"""


class StatSha:
    """Stat-keyed sha256 memo kept in the principal's stat-sha.sqlite (works with the index flag off; never creates index.sqlite): a file's
    bytes are hashed once; a later ask reuses the sha only while (size, mtime_ns, ctime_ns, inode) are all
    unchanged. Any change, including a `touch -r` that restores mtime (ctime moves), means re-read.
    A file touched in the last RACY_NS is never memoized, so a write inside the clock tick cannot be missed."""
    RACY_NS = 50_000_000
    TABLE = ("CREATE TABLE IF NOT EXISTS statsha(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, "
             "ctime_ns INTEGER, ino INTEGER, sha256 TEXT)")

    def __init__(self, db_path):
        import threading
        self.path = Path(db_path)
        self.rows, self.pending, self.loaded = {}, {}, False
        self.lock = threading.Lock()

    @staticmethod
    def key(path):
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)

    def _load(self):
        if self.loaded:
            return
        self.loaded = True
        if not self.path.is_file():
            return
        try:
            db = sqlite3.connect(str(self.path), timeout=5)
            try:
                for r in db.execute("SELECT path,size,mtime_ns,ctime_ns,ino,sha256 FROM statsha"):
                    self.rows[r[0]] = (r[1:5], r[5])
            finally:
                db.close()
        except sqlite3.Error:
            pass  # no memo yet: every file is read once

    def get(self, path):
        """(key, sha or None): the memoized sha when the file's stat still matches, else None."""
        k = self.key(path)  # OSError (gone / unreadable) goes to the caller, as a read would
        with self.lock:
            self._load()
            hit = self.rows.get(path)
        return k, (hit[1] if hit and hit[0] == k else None)

    def put(self, path, k, sha):
        if time.time_ns() - max(k[1], k[2]) < self.RACY_NS:
            return
        with self.lock:
            self.rows[path] = (k, sha)
            self.pending[path] = (k, sha)

    def flush(self):
        with self.lock:
            batch, self.pending = self.pending, {}
        if not batch:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), timeout=5)
            try:
                db.execute(self.TABLE)
                db.executemany("INSERT OR REPLACE INTO statsha VALUES(?,?,?,?,?,?)",
                               [(p, *k, s) for p, (k, s) in batch.items()])
                db.commit()
            finally:
                db.close()
        except sqlite3.Error:
            pass  # the memo is an optimization only


def _has_secret(text: str) -> bool:
    from prepare_bulk import has_secret
    return has_secret(text)


def _state_dir(principal: str) -> Path:
    from dispatch import state_root
    return state_root() / principal


class FileIndex:
    def __init__(self, principal: str, db_path=None):
        self.principal = principal
        self.path = Path(db_path) if db_path else _state_dir(principal) / "index.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)
        try:
            self.db.executescript(FTS_SCHEMA)
            self.fts_error = None
        except sqlite3.Error as e:  # no FTS5 / no contentless delete in this sqlite: the ask runs the S3a path
            self.fts_error = f"{type(e).__name__}: {str(e)[:80]}"
        have = {r[1] for r in self.db.execute("PRAGMA table_info(pointers)")}
        for col, typ in (("status", "TEXT"), ("generation", "TEXT"), ("views", "TEXT"), ("listed", "INTEGER DEFAULT 0"), ("entries", "INTEGER"), ("complete", "INTEGER")):
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
        if not self.fts_error and self.db.execute("SELECT 1 FROM fts_map WHERE path=?", (path,)).fetchone():
            self.fts_drop(path)  # its passages go with it; the updater's FTS pass re-adds a file that still qualifies
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('fts_ok','0')")
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
                if p not in known and self.owner_of(p, any_row=True) not in (None, pointer):
                    continue  # another pointer holds this path (reviewed or edited): a walk never takes it away
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
        self.db.execute("INSERT INTO pointers(pointer,stale,roots,checked_at,entries) VALUES(?,?,?,strftime('%s','now'),?) "
                        "ON CONFLICT(pointer) DO UPDATE SET stale=excluded.stale, roots=excluded.roots, checked_at=excluded.checked_at, "
                        "entries=excluded.entries",
                        (pointer, stale, json.dumps(list(roots)) if roots else None, len(entries)))
        self.db.commit()
        return out

    def owner_of(self, path: str, any_row: bool = False):
        """The pointer whose row holds this path (`files`, or `seen` as an edited file; any_row: any `seen` row), or None.
        A path has one row, so a file two pointers list belongs to the one that was updated last."""
        r = self.db.execute("SELECT pointer FROM files WHERE path=?", (path,)).fetchone()
        if r:
            return r[0]
        r = self.db.execute("SELECT pointer FROM seen WHERE path=?" + ("" if any_row else " AND reason='edited'"), (path,)).fetchone()
        return r[0] if r else None

    def set_complete(self, expected: dict) -> None:
        """Updater: per pointer, 1 when every reviewed file of its prepare-cache that still exists is held by the index
        under THIS pointer (a `files` row, or `seen` as edited / held), else 0. `expected`: {pointer: [paths]}."""
        for ptr, paths in expected.items():
            have = {r[0] for r in self.db.execute("SELECT path FROM files WHERE pointer=?", (ptr,))}
            have |= {r[0] for r in self.db.execute("SELECT path FROM seen WHERE pointer=? AND reason IN ('edited','held')", (ptr,))}
            self.db.execute("UPDATE pointers SET complete=? WHERE pointer=?", (1 if all(p in have for p in paths) else 0, ptr))
        self.db.commit()

    def coverage(self) -> dict:
        """{pointer: {"stale", "status", "generation", "entries", "files", "complete"}} for every listed pointer:
        what the ask needs to decide, pointer by pointer, whether the index holds it completely and currently."""
        out = {}
        for name, stale, status, gen, entries, complete in self.db.execute(
                "SELECT pointer,stale,status,generation,entries,complete FROM pointers WHERE listed=1"):
            out[name] = {"stale": bool(stale), "status": status or "", "generation": json.loads(gen) if gen else None,
                         "entries": entries, "complete": complete}
        for name, n in self.db.execute("SELECT pointer,COUNT(*) FROM files GROUP BY pointer"):
            if name in out:
                out[name]["files"] = n
        for v in out.values():
            v.setdefault("files", 0)
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
        """[(pointer, path, entry)] of every indexed file (and every edited one) of these pointers, entry shaped like a prepare-cache
        record (pass, sha256 = the reviewed sha, and the labels the TOC search reads)."""
        out = []
        for ptr in pointers:
            for path, sha, tj in self.db.execute(
                    "SELECT f.path,f.reviewed_sha,t.json FROM files f LEFT JOIN toc t ON t.path=f.path WHERE f.pointer=? ORDER BY f.path",
                    (ptr,)):
                out.append((ptr, path, {**(json.loads(tj) if tj else {}), "pass": True, "sha256": sha}))
            # edited since review (a few files): listed like today's candidate_files does, with the reviewed sha,
            # so the caller's read_sha / edited_readable rule decides whether the current text is searched
            for path, sha in self.db.execute(
                    "SELECT path,reviewed_sha FROM seen WHERE pointer=? AND reason='edited' AND reviewed_sha IS NOT NULL ORDER BY path", (ptr,)):
                out.append((ptr, path, {"pass": True, "sha256": sha}))
        return out

    # ---- S3b: FTS5 passages (updater writes; the ask only reads) ----

    def fts_have(self) -> dict:
        """{path: (sha, pointer, labels)} of what the FTS table holds now."""
        return {p: (sha, ptr, lab) for p, ptr, sha, lab in self.db.execute("SELECT path,pointer,sha,lab FROM fts_map")}

    def fts_begin(self) -> None:
        """An unfinished pass (crash) leaves the table marked not-ok, so the ask runs the S3a path."""
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('fts_ok','0')")
        self.db.commit()

    def _vocab_add(self, words, d: int) -> None:
        for w in words:
            if d > 0:
                new = self.db.execute("INSERT INTO fvocab VALUES(?,1) ON CONFLICT(word) DO UPDATE SET n=n+1 RETURNING n", (w,)).fetchone()[0] == 1
                if new:
                    self.db.executemany("INSERT OR IGNORE INTO vbigram VALUES(?,?)", [(g, w) for g in {w[i:i + 2] for i in range(len(w) - 1)}])
            else:
                if self.db.execute("UPDATE fvocab SET n=n-1 WHERE word=? RETURNING n", (w,)).fetchone()[0] <= 0:
                    self.db.execute("DELETE FROM fvocab WHERE word=?", (w,))
                    self.db.execute("DELETE FROM vbigram WHERE word=?", (w,))

    def fts_drop(self, path: str) -> None:
        r = self.db.execute("SELECT rid,words FROM fts_map WHERE path=?", (path,)).fetchone()
        if r:
            self._vocab_add((r[1] or "").split(), -1)
            self.db.execute("DELETE FROM fts WHERE rowid=?", (r[0],))
            self.db.execute("DELETE FROM fts_map WHERE rid=?", (r[0],))
        self.db.execute("DELETE FROM witems WHERE path=?", (path,))
        self.db.execute("DELETE FROM tocpage WHERE path=?", (path,))

    def fts_put(self, path, pointer, person, sha, lab, body, toc, npass, psize, item_json, toc_json) -> None:
        """Replace one file's rows (and only that file's)."""
        self.fts_drop(path)
        cur = self.db.execute("INSERT INTO fts_map(path,pointer,person,sha,lab,npass,psize,words) VALUES(?,?,?,?,?,?,?,?)",
                              (path, pointer, person or "", sha, lab, npass, psize, " ".join(sorted(set(body.split())))))
        self._vocab_add(set(body.split()), 1)
        self.db.execute("INSERT INTO fts(rowid,body,toc) VALUES(?,?,?)", (cur.lastrowid, body, toc))
        self.db.execute("INSERT INTO witems VALUES(?,?,?)", (path, sha, item_json))
        if toc_json is not None:
            self.db.execute("INSERT INTO tocpage VALUES(?,?,?)", (path, sha, toc_json))

    def fts_finish(self, version: str) -> None:
        self.db.execute("DELETE FROM fts_stats")
        self.db.execute("INSERT INTO fts_stats SELECT pointer,person,COUNT(*),SUM(npass),SUM(psize) FROM fts_map GROUP BY pointer,person")
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('fts_version',?)", (version,))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('fts_ok','1')")
        self.db.commit()

    def fts_usable(self, version: str):
        """(True, "") or (False, why): the table exists, was fully built, and by this tokenizer version."""
        if self.fts_error:
            return False, f"fts unavailable ({self.fts_error})"
        m = dict(self.db.execute("SELECT k,v FROM meta WHERE k IN ('fts_ok','fts_version')"))
        if m.get("fts_ok") != "1":
            return False, "fts not built" if "fts_ok" not in m else "fts stale (update unfinished)"
        if m.get("fts_version") != version:
            return False, "fts stale (tokenizer version changed)"
        return True, ""

    def fts_vocab(self) -> list:
        return [r[0] for r in self.db.execute("SELECT word FROM fvocab")]

    def fts_variants(self, terms, synonyms) -> dict:
        """ask.term_variants() over the stored vocabulary, without scanning it: close spellings are looked up by shared
        bigram (a ratio >= 0.8 spelling always shares one), stem forms by key range."""
        import difflib
        out = {}
        for t in terms:
            near = set()
            if len(t) > 3:
                grams = list({t[i:i + 2] for i in range(len(t) - 1)})
                pool = [r[0] for r in self.db.execute(
                    f"SELECT DISTINCT word FROM vbigram WHERE bg IN ({','.join('?' * len(grams))})", grams)]
                near = set(difflib.get_close_matches(t, pool, n=3, cutoff=0.8))
            stem = t[:5] if len(t) > 5 else t
            lim = 99 if len(t) > 5 else 2
            out[t] = near | {w for (w,) in self.db.execute("SELECT word FROM fvocab WHERE word>=? AND word<?", (stem, stem + "\U0010ffff"))
                             if len(w) - len(t) <= lim}
            out[t] |= {x for x in synonyms.get(t, []) if self.db.execute("SELECT 1 FROM fvocab WHERE word=?", (x,)).fetchone()}
        return out

    @staticmethod
    def _scope(pointers, who):
        sql, a = f" AND m.pointer IN ({','.join('?' * len(pointers))})", list(pointers)
        if who:
            sql += f" AND (m.person='' OR m.person IN ({','.join('?' * len(who))}))"
            a += list(who)
        return sql, a

    def fts_stats_for(self, pointers, who=()) -> dict:
        """Global counts over the searched pointers (and the resolved person's folders): files, passages, passage size."""
        sql = f"SELECT COALESCE(SUM(n),0),COALESCE(SUM(passages),0),COALESCE(SUM(psize),0) FROM fts_stats m WHERE 1=1"
        s, a = self._scope(pointers, who)
        n, passages, size = self.db.execute(sql + s, a).fetchone()
        return {"n": n, "passages": passages, "size": size}

    def fts_df(self, match: str, pointers, who=()) -> int:
        s, a = self._scope(pointers, who)
        return self.db.execute("SELECT COUNT(*) FROM fts JOIN fts_map m ON m.rid=fts.rowid WHERE fts MATCH ?" + s, [match, *a]).fetchone()[0]

    def fts_top(self, match: str, pointers, k: int, who=()) -> list:
        """The k best paths by bm25 for an FTS match (column filters are in the match string)."""
        s, a = self._scope(pointers, who)
        return [r[0] for r in self.db.execute(
            "SELECT m.path FROM fts JOIN fts_map m ON m.rid=fts.rowid WHERE fts MATCH ?" + s + " ORDER BY bm25(fts), m.path LIMIT ?",
            [match, *a, k])]

    def fts_first_paths(self, pointers, k: int, who=()) -> list:
        """The k first indexed paths in path order: the TOC shortlist's tie-break, for a question that hits few files."""
        s, a = self._scope(pointers, who)
        s = s.replace("m.pointer IN", "m.pointer||'' IN").replace("m.person=''", "m.person||''=''").replace("m.person IN", "m.person||'' IN")
        # (the || keeps the planner on the path index: first k in path order, not a sort of every row)
        return [r[0] for r in self.db.execute("SELECT m.path FROM fts_map m WHERE 1=1" + s + " ORDER BY m.path LIMIT ?", [*a, k])]

    def has_entries(self, pointer: str) -> bool:
        """Does the pointer have a prepare-cache with entries (as the updater last saw it)? An index made before this
        was recorded answers from the cache file instead."""
        try:
            r = self.db.execute("SELECT entries FROM pointers WHERE pointer=?", (pointer,)).fetchone()
        except sqlite3.Error:  # locked by the updater: the cache file says it too
            r = None
        if r and r[0] is not None:
            return r[0] > 0
        from prepare_bulk import load_cache_files
        return bool(load_cache_files(pointer))

    def person_paths(self, pointers) -> list:
        """One path per person folder per pointer plus the PROFILE files, from the stored per-person groups: no
        prepare-cache is parsed and no scan of every file. Held / edited / unreviewed files count too (`seen`, small)."""
        q = ",".join("?" * len(pointers))
        out = [r[0] for r in self.db.execute(
            f"SELECT path FROM seen WHERE pointer IN ({q}) AND path LIKE '%/agents/global/documents/%'", list(pointers))]
        for ptr, who in self.db.execute(f"SELECT pointer,person FROM fts_stats WHERE pointer IN ({q}) AND person!=''", list(pointers)).fetchall():
            out += [r[0] for r in self.db.execute("SELECT path FROM fts_map WHERE pointer=? AND person=? LIMIT 1", (ptr, who))]
            out += [r[0] for r in self.db.execute("SELECT path FROM fts_map WHERE pointer=? AND person=? AND path LIKE '%/profile.%'", (ptr, who))]
        return out

    def fts_rows(self, paths) -> list:
        """[(pointer, path, entry, item, tocrow)] for these indexed paths: entry shaped like candidates()'s."""
        out = []
        paths = list(paths)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            q = ",".join("?" * len(chunk))
            for ptr, path, sha, tj, ij, pj in self.db.execute(
                    f"SELECT f.pointer,f.path,f.reviewed_sha,t.json,w.json,c.json FROM files f LEFT JOIN toc t ON t.path=f.path "
                    f"LEFT JOIN witems w ON w.path=f.path AND w.sha=f.sha256 LEFT JOIN tocpage c ON c.path=f.path AND c.sha=f.sha256 "
                    f"WHERE f.path IN ({q})", chunk):
                out.append((ptr, path, {**(json.loads(tj) if tj else {}), "pass": True, "sha256": sha},
                            json.loads(ij) if ij else None, json.loads(pj) if pj else None))
        return out

    def edited_candidates(self, pointers) -> list:
        """[(pointer, path, entry)] of files edited since review (the few `seen` rows), searched at read time."""
        out = []
        for ptr in pointers:
            for path, sha in self.db.execute(
                    "SELECT path,reviewed_sha FROM seen WHERE pointer=? AND reason='edited' AND reviewed_sha IS NOT NULL ORDER BY path", (ptr,)):
                out.append((ptr, path, {"pass": True, "sha256": sha}))
        return out

    def mark_stale(self, pointer: str) -> None:
        self.db.execute("UPDATE pointers SET stale=1 WHERE pointer=?", (pointer,))
        self.db.commit()

    def purge(self, pointer: str) -> None:
        """Unshare / refresh: DELETE every row of the pointer (files, toc, seen, pointers)."""
        if not self.fts_error:
            for (p,) in self.db.execute("SELECT path FROM fts_map WHERE pointer=?", (pointer,)).fetchall():
                self.fts_drop(p)
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('fts_ok','0')")
        self.db.execute("DELETE FROM toc WHERE path IN (SELECT path FROM files WHERE pointer=?)", (pointer,))
        for t in ("files", "seen", "pointers"):
            self.db.execute(f"DELETE FROM {t} WHERE pointer=?", (pointer,))
        self.db.commit()
