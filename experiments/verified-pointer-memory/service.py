"""Trusted-local verified pointer/cache experiment."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "3"
STATUSES = {"ready", "no-match", "preparation-required", "refused", "error"}
SNAPSHOT_FRESHNESS = {'mode': 'snapshot'}


_REGISTRY_CACHE: dict[str, tuple[tuple[int, int, int], Any]] = {}


def invalidate_registry(path: str | Path) -> None:
    """Drop the parsed copy of a registry file (call after writing it in-process)."""
    _REGISTRY_CACHE.pop(str(path), None)


def load_registry(path: str | Path) -> Any:
    """Parsed registry JSON, re-parsed only when (size, mtime_ns, inode) changes.

    No registry file means nothing is connected: the first connect writes it, so a set-up install
    has none until then, and it reads as an empty registry.
    The returned object is shared: callers must deepcopy anything they keep or change."""
    key_path = str(path)
    try:
        st = Path(path).stat()
    except FileNotFoundError:
        return {'version': 1, 'datasets': {}}
    key = (st.st_size, st.st_mtime_ns, st.st_ino)
    hit = _REGISTRY_CACHE.get(key_path)
    if hit and hit[0] == key:
        return hit[1]
    # Stat happens before the read: if the file changes in between, the stored key is
    # older than the data, so the next call re-parses.
    parsed = json.loads(Path(path).read_bytes())
    _REGISTRY_CACHE[key_path] = (key, parsed)
    return parsed


def pack(value: Any) -> str:
    """Serialize a value deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value: Any) -> str:
    """Return a deterministic SHA-256 digest for a JSON value."""
    return hashlib.sha256(pack(value).encode()).hexdigest()


def require_text(label: str, value: Any, allow_empty: bool = False) -> str:
    """Validate a string input and return it."""
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(
            f'{label} must be a {"string" if allow_empty else "nonempty string"}'
        )
    return value


def require_time(label: str, value: Any) -> float:
    """Validate a finite Unix-seconds value."""
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f'{label} must be finite Unix seconds')
    return float(value)


def normalize_freshness(freshness: Any = None) -> dict[str, Any]:
    """Return the supported request freshness policy, rejecting ambiguous input."""
    if freshness is None:
        return SNAPSHOT_FRESHNESS.copy()
    if not isinstance(freshness, dict):
        raise ValueError('freshness must be an object')
    mode = freshness.get('mode')
    if mode == 'snapshot' and set(freshness) == {'mode'}:
        return SNAPSHOT_FRESHNESS.copy()
    if mode == 'current' and set(freshness) == {'mode', 'maxAgeSeconds'}:
        max_age = freshness['maxAgeSeconds']
        if (isinstance(max_age, bool) or not isinstance(max_age, (int, float))
                or not math.isfinite(max_age) or max_age <= 0):
            raise ValueError('freshness.maxAgeSeconds must be a positive finite number')
        return {'mode': 'current', 'maxAgeSeconds': float(max_age)}
    raise ValueError('freshness must be {"mode":"snapshot"} or {"mode":"current","maxAgeSeconds":positive finite number}')


def valid_v2_schema(connection: sqlite3.Connection, tables: set[str]) -> bool:
    """Recognize only the exact schema produced by version 2 of this service."""
    expected_columns = {
        'schema_meta': [('singleton', 'INTEGER', 0, 1),
                        ('version', 'TEXT', 1, 0)],
        'pointers': [('name', 'TEXT', 0, 1), ('body', 'TEXT', 1, 0)],
        'cache': [('k', 'TEXT', 0, 1), ('pointer', 'TEXT', 1, 0),
                  ('generation', 'TEXT', 1, 0), ('fingerprint', 'TEXT', 1, 0),
                  ('body', 'TEXT', 1, 0)],
        'pending': [('ticket', 'TEXT', 0, 1), ('pointer', 'TEXT', 1, 0),
                    ('generation', 'TEXT', 1, 0), ('fingerprint', 'TEXT', 1, 0),
                    ('body', 'TEXT', 1, 0)],
    }
    if tables != set(expected_columns):
        return False
    for table, expected in expected_columns.items():
        actual = [(row[1], row[2].upper(), row[3], row[5]) for row in
                  connection.execute(f'PRAGMA table_info({table})')]
        if actual != expected:
            return False
    schema_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()[0]
    if 'CHECK(singleton = 1)' not in schema_sql:
        return False
    expected_indexes = {
        'schema_meta': [],
        'pointers': [('sqlite_autoindex_pointers_1', 1, 'pk', ['name'])],
        'cache': [('cache_pointer', 0, 'c', ['pointer']),
                  ('sqlite_autoindex_cache_1', 1, 'pk', ['k'])],
        'pending': [('pending_pointer', 0, 'c', ['pointer']),
                    ('sqlite_autoindex_pending_1', 1, 'pk', ['ticket'])],
    }
    for table, expected in expected_indexes.items():
        actual = []
        for row in connection.execute(f'PRAGMA index_list({table})'):
            columns = [item[2] for item in
                       connection.execute(f'PRAGMA index_info({row[1]})')]
            actual.append((row[1], row[2], row[3], columns))
        if sorted(actual) != sorted(expected):
            return False
    return True


# An agent name: letters, digits, ".", "_", "-", starting with a letter or digit. A space or a path
# separator made a second, empty identity ("sales " beside "sales") and could point an
# agent's state folder at another agent's ("x/../primary").
PRINCIPAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid_principal(name) -> bool:
    return isinstance(name, str) and PRINCIPAL_RE.fullmatch(name) is not None

class WatchedRefused(ValueError):
    """The one watched-folder rule said no. `files` are the caller's own paths; `message` names the watched pointer
    and the exact way to clear it, never another pointer's agents or contents."""

    def __init__(self, message: str, files: list[str] | None = None):
        super().__init__(message)
        self.message, self.files = message, files or []

    def answer(self) -> dict[str, Any]:
        return {'status': 'error', 'reason': 'watched-refused', 'message': self.message, 'files': self.files}


def _ids(folders) -> set:
    """(device, inode) of each folder that exists. Identity, not spelling: Unicode forms, case and firmlink paths
    (/System/Volumes/Data/...) of one folder all give the same pair. (A hard link to a file is a known limit.)"""
    out = set()
    for f in folders:
        try:
            st = os.stat(f)
        except OSError:
            continue  # a folder that is gone holds nothing
        out.add((st.st_dev, st.st_ino))
    return out


def _inside(path: str, ids: set) -> bool:
    """Whether `path` is, or lies under, one of the folders `ids` names: each parent is compared by identity."""
    cur = os.path.realpath(path)
    while True:
        try:
            st = os.stat(cur)
            if (st.st_dev, st.st_ino) in ids:
                return True
        except OSError:
            pass  # not there (yet): its parents still decide
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _originals(entry) -> list[str]:
    return [o['path'] for o in (entry or {}).get('originals', []) if isinstance(o, dict) and o.get('path')]


class Service:
    """Store verified dataset pointers, review tickets, and approved results."""

    def __init__(
        self,
        db: str | Path,
        registry: str | Path,
        retrieve: Callable[[str, str], dict[str, Any]],
        *,
        navigate_provider: Callable[[str, dict[str, Any], Any], dict[str, Any]] | None = None,
        navigate_many_provider: Callable[[str, list, Any], Any] | None = None,
        cache_ttl_seconds: float | None = None,
        review_ttl_seconds: float = 600,
    ) -> None:
        # cache_ttl_seconds None (the default): a saved answer has no clock expiry and lasts
        # until its source changes. An operator may still set a positive finite limit.
        ttl_values = (
            ("cache_ttl_seconds", cache_ttl_seconds),
            ("review_ttl_seconds", review_ttl_seconds),
        )
        for label, value in ttl_values:
            if value is None and label == "cache_ttl_seconds":
                continue
            invalid_type = isinstance(value, bool) or not isinstance(value, (int, float))
            if invalid_type or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive finite number")
        self.db = str(db)
        self.registry = Path(registry)
        self.retrieve = retrieve
        self.navigate_provider = navigate_provider
        self.navigate_many_provider = navigate_many_provider
        self.cache_ttl_seconds = None if cache_ttl_seconds is None else float(cache_ttl_seconds)
        self.review_ttl_seconds = float(review_ttl_seconds)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in rows}
            if tables:
                if 'schema_meta' not in tables:
                    raise ValueError(
                        'legacy verified-pointer-memory database; create a new v2 '
                        'database (automatic migration is unsupported)')
                versions = c.execute(
                    'SELECT version FROM schema_meta'
                ).fetchall()
                if versions == [('2',)]:
                    if not valid_v2_schema(c, tables):
                        raise ValueError('invalid version 2 database layout')
                    c.execute('CREATE TABLE attempts(attempt TEXT PRIMARY KEY, '
                              'pointer TEXT NOT NULL, generation TEXT, '
                              'fingerprint TEXT, body TEXT NOT NULL)')
                    c.execute('CREATE INDEX attempts_pointer ON attempts(pointer)')
                    c.execute('UPDATE schema_meta SET version=?', (SCHEMA_VERSION,))
                    return
                if versions != [(SCHEMA_VERSION,)]:
                    raise ValueError(
                        'unsupported verified-pointer-memory schema; expected '
                        f'version {SCHEMA_VERSION}')
                return
            statements = (
                'CREATE TABLE schema_meta('
                'singleton INTEGER PRIMARY KEY CHECK(singleton = 1), '
                'version TEXT NOT NULL)',
                'CREATE TABLE pointers(name TEXT PRIMARY KEY, body TEXT NOT NULL)',
                'CREATE TABLE cache(k TEXT PRIMARY KEY, pointer TEXT NOT NULL, '
                'generation TEXT NOT NULL, fingerprint TEXT NOT NULL, '
                'body TEXT NOT NULL)',
                'CREATE INDEX cache_pointer ON cache(pointer)',
                'CREATE TABLE pending(ticket TEXT PRIMARY KEY, '
                'pointer TEXT NOT NULL, generation TEXT NOT NULL, '
                'fingerprint TEXT NOT NULL, body TEXT NOT NULL)',
                'CREATE INDEX pending_pointer ON pending(pointer)',
                'CREATE TABLE attempts(attempt TEXT PRIMARY KEY, '
                'pointer TEXT NOT NULL, generation TEXT, fingerprint TEXT, '
                'body TEXT NOT NULL)',
                'CREATE INDEX attempts_pointer ON attempts(pointer)',
            )
            for statement in statements:
                c.execute(statement)
            c.execute(
                'INSERT INTO schema_meta(singleton, version) VALUES (1, ?)',
                (SCHEMA_VERSION,),
            )

    def connect(self) -> sqlite3.Connection:
        """Open the service's local SQLite database."""
        return sqlite3.connect(self.db, timeout=30)

    def _manifest(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Hash and parse the same manifest bytes."""
        raw = Path(entry['manifestPath']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['manifestSHA256']:
            raise ValueError('stale manifest')
        return json.loads(raw)

    def snapshot(self, dataset: str) -> dict[str, Any]:
        """Validate and describe the current reviewed dataset state."""
        entry = copy.deepcopy(load_registry(self.registry)['datasets'][dataset])
        manifest = self._manifest(entry)
        for original in entry['originals']:
            if sha(original['path']) != original['sha256']:
                raise ValueError('stale original')
        for source in manifest['sources']:
            if sha(source['path']) != source['contentSHA']:
                raise ValueError('stale source')
        # The shareable mark is who-may-see policy, not content: changing it must not stale the pointer.
        entry = {k: v for k, v in entry.items() if k != 'shareable'}
        return {'entry': entry, 'sources': manifest['sources']}

    def recipe(self, name: str, principal: str) -> dict[str, Any]:
        """The recorded connect request for a pointer, readable while it is stale.

        A dataset connected before recipes were recorded, through the path connector,
        yields one rebuilt from its prepared manifest (original paths and descriptions).
        Any other dataset (built by hand) has none."""
        require_text('pointer', name)
        require_text('principal', principal)
        with self.connect() as c:
            row = c.execute('SELECT body FROM pointers WHERE name=?', (name, )).fetchone()
        if not row:
            return {'status': 'unknown-pointer'}
        pointer = json.loads(row[0])
        if principal not in pointer['principals']:
            return {'status': 'access-denied'}
        entry = copy.deepcopy(load_registry(self.registry)['datasets'].get(pointer['dataset']) or {})
        recipe = entry.get('recipe')
        if not recipe and entry.get('pathConnection'):
            try:
                manifest = self._manifest(entry)
            except (OSError, ValueError, KeyError):
                return {'status': 'no-recipe'}
            policies = entry.get('viewPolicies', {})
            if any(not any(s.get('originalPath') == path and s.get('transformSHA') == digest
                           and 'viewTransform' in s for s in manifest['sources'])
                   for path, digest in policies.items()):
                return {'status': 'no-recipe'}
            recipe = {'pointer': name, 'dataset': pointer['dataset'],
                      'principals': sorted(pointer['principals']),
                      'structure': entry.get('structure', 'flat-files'),
                      'sources': [{'path': s['originalPath'], 'id': s['id'],
                                   'description': s.get('description', ''),
                                   **({'viewTransform': s['viewTransform']} if 'viewTransform' in s else {})}
                                  for s in manifest['sources'] if s.get('originalPath')]}
        if not recipe or not recipe.get('sources'):
            return {'status': 'no-recipe'}
        return {'status': 'ok', 'recipe': recipe}

    def register(self, name: str, dataset: str, principals: list[str]) -> None:
        """Register a pointer and clear data from its previous generation."""
        require_text('pointer', name)
        require_text('dataset', dataset)
        if not isinstance(principals, list) or not all(valid_principal(p) for p in principals):
            raise ValueError('principals must be agent names (letters, digits, ".", "_", "-")')
        snapshot = self.snapshot(dataset)
        row = {
            'dataset': dataset,
            'principals': sorted(principals),
            'generation': str(uuid.uuid4()),
            'fingerprint': digest(snapshot),
            'snapshot': snapshot
        }
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = self._rows(c)
            self.require_allowed(name, principals, _originals(snapshot['entry']), rows)
            kept = (rows.get(name) or {}).get('watched')
            if kept:  # a refresh keeps the mark
                row['watched'] = kept
            c.execute('DELETE FROM cache WHERE pointer=?', (name, ))
            c.execute('DELETE FROM pending WHERE pointer=?', (name, ))
            c.execute('INSERT OR REPLACE INTO pointers VALUES (?,?)',
                      (name, pack(row)))

    # --- watched folders: ONE rule, enforced here, where every connect and register ends ---------------------
    # A pointer may be marked watched (its folders are stored on its row). A file whose REAL path lies under a
    # watched pointer's folders may only be registered to a pointer whose agents are a SUBSET of the watched
    # pointer's agents. Judged on real paths and agent sets only: never on names or on any report.

    @staticmethod
    def _rows(c: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        return {n: json.loads(b) for n, b in c.execute('SELECT name, body FROM pointers ORDER BY name')}

    @staticmethod
    def watched_refusal(name: str, principals: list[str], paths: list[str], rows: dict[str, dict[str, Any]]):
        """None when allowed, else a WatchedRefused. `rows` are the registered pointers."""
        mine = set(principals)
        for other, row in rows.items():
            folders = row.get('watched')
            if other == name or not folders or mine <= set(row['principals']):
                continue
            ids = _ids(folders)
            files = [p for p in paths if _inside(p, ids)]
            if files:
                return WatchedRefused(
                    f'cannot register {name}: {len(files)} of its files lie inside the watched folder of {other}, '
                    f'which serves other agents. Refused. To clear it: connect {name} for agents within those of '
                    f'{other}, or run: python3 prepare_bulk.py --unwatch --pointer {other} --principal AGENT '
                    f'(its own agents)', files)
        mine_row = rows.get(name) or {}
        if mine_row.get('watched') and not mine <= set(mine_row['principals']):
            return WatchedRefused(
                f'cannot change the agents of {name}, a watched folder, to ones it does not already serve. Refused. '
                f'To clear it: python3 prepare_bulk.py --unwatch --pointer {name} --principal AGENT (its own agents) '
                f'first, change them, then watch it again')
        if mine_row.get('watched'):  # a subset: pointers holding files inside it for agents it would drop are refused
            held = Service._outside_holders(name, principals, mine_row['watched'], rows)
            if held:
                return WatchedRefused(
                    f'cannot change the agents of {name}, a watched folder: {", ".join(held)} hold files inside it '
                    f'for agents it would no longer serve. Refused. To clear it: python3 prepare_bulk.py --unwatch '
                    f'--pointer {name} --principal AGENT (its own agents) first')
        return None

    @staticmethod
    def _outside_holders(name: str, principals: list[str], folders, rows: dict[str, dict[str, Any]]) -> list[str]:
        mine, ids = set(principals), _ids(folders)
        return [n for n, row in rows.items()
                if n != name and not set(row['principals']) <= mine
                and any(_inside(p, ids) for p in _originals(row.get('snapshot', {}).get('entry')))]

    def require_allowed(self, name: str, principals: list[str], paths: list[str], rows: dict[str, dict[str, Any]]) -> None:
        refusal = self.watched_refusal(name, principals, paths, rows)
        if refusal:
            raise refusal

    def check_watched(self, name: str, principals: list[str], paths: list[str]) -> dict[str, Any]:
        """Read-only dry run of the same rule, for a caller that wants to leave out the files it may not register."""
        if not isinstance(principals, list) or not principals or not all(valid_principal(p) for p in principals):
            raise ValueError('principals must be a non-empty list of agent names')
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise ValueError('paths must be a list of file paths')
        with self.connect() as c:
            rows = self._rows(c)
        refusal = self.watched_refusal(name, principals, paths, rows)
        if refusal is None:
            return {'status': 'ok', 'refused': []}
        return {'status': 'ok', 'refused': [{'path': p} for p in refusal.files], 'message': refusal.message}

    def watch(self, name: str, principals: list[str], folders: list[str], on: bool = True) -> dict[str, Any]:
        """Mark (or clear the mark of) a registered pointer. A mark is refused when another pointer holds a file
        under the folders for agents the watched pointer does not serve; the answer names pointers only."""
        require_text('pointer', name)
        real: list[str] = []
        if on:
            real = [os.path.realpath(f) for f in folders] if isinstance(folders, list) and folders else []
            if not real or any(r == os.sep or not os.path.isdir(r) for r in real):
                raise ValueError('folders must be real folders, not the filesystem root')

        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = self._rows(c)
            row = rows.get(name)
            if row is None:
                return {'status': 'error', 'reason': 'unknown-pointer'}
            if set(principals) != set(row['principals']):
                return {'status': 'error', 'reason': 'principals must be the agents this pointer serves'}
            if on:
                own = _originals(row.get('snapshot', {}).get('entry'))
                if any(not any(_inside(o, _ids([r])) for o in own) for r in real):
                    return {'status': 'error', 'reason': 'each folder must hold one of this pointer\'s own files'}
                held = self._outside_holders(name, row['principals'], real, rows)
                if held:
                    return {'status': 'error', 'reason': 'watched-refused', 'pointers': held,
                            'message': f'cannot mark {name} watched: pointers inside its folders hold files for '
                                       f'agents it does not serve ({", ".join(held)}). Not marked.'}
                row['watched'] = sorted(set(real))
            else:
                row.pop('watched', None)
            c.execute('UPDATE pointers SET body=? WHERE name=?', (pack(row), name))
        return {'status': 'ok', 'watched': on}

    def remove(self, name: str) -> None:
        """Remove a pointer and all of its pending and cached data."""
        require_text('pointer', name)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM cache WHERE pointer=?', (name, ))
            c.execute('DELETE FROM pending WHERE pointer=?', (name, ))
            c.execute('DELETE FROM pointers WHERE name=?', (name, ))

    def pointer(
        self, name: str, principal: str
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        """Return an authorized, fresh pointer or a status object."""
        require_text('pointer', name)
        require_text('principal', principal)
        with self.connect() as c:
            row = c.execute('SELECT body FROM pointers WHERE name=?',
                            (name, )).fetchone()
        if not row:
            return None, {'status': 'unknown-pointer'}
        pointer = json.loads(row[0])
        if principal not in pointer['principals']:
            return None, {'status': 'access-denied'}
        try:
            current = self.snapshot(pointer['dataset'])
        except (OSError, ValueError, KeyError, TypeError):
            return None, {'status': 'preparation-required'}
        if digest(current) != pointer['fingerprint']:
            return None, {'status': 'preparation-required'}
        return pointer, None

    def _last_good(self, name: str, principal: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """A stale pointer's registered binding and what changed since it was registered.

        Navigation only: the registered catalog holds the descriptions reviewed at connect,
        so routing on it sends the provider nothing new. Reviewed passages, tickets and the
        cache stay behind pointer(), which refuses a stale pointer."""
        stored = self._stored_pointer(name, principal)
        if stored is None:
            return None, {}
        snapshot = stored.get('snapshot') or {}
        changed, missing = set(), set()
        for original in (snapshot.get('entry') or {}).get('originals') or []:
            try:
                if sha(original['path']) != original['sha256']:
                    changed.add(original['path'])
            except OSError:
                missing.add(original['path'])
            except (KeyError, TypeError):
                continue
        for source in snapshot.get('sources') or []:
            try:
                if sha(source['path']) != source['contentSHA']:
                    changed.add(source.get('originalPath', source['path']))
            except OSError:
                missing.add(source.get('originalPath', source['path']))
            except (KeyError, TypeError):
                continue
        return stored, {'status': 'preparation-required', 'changed': sorted(changed - missing),
                        'missing': sorted(missing)}

    def key(
        self, pointer: dict[str, Any], question: str, principal: str, context: str,
        freshness: dict[str, Any] | None = None,
    ) -> str:
        """Build a request key scoped to an immutable generation."""
        freshness = normalize_freshness(freshness)
        # Preserve the v2 default/snapshot cache key so saved entries remain usable.
        key = [pointer['generation'], question, principal, context]
        if freshness != SNAPSHOT_FRESHNESS:
            key.append(freshness)
        return digest(key)

    def freshness(self, pointer: dict[str, Any], policy: dict[str, Any], now: float):
        """Return caller-visible freshness metadata or require an upstream refresh."""
        if policy['mode'] == 'snapshot':
            return SNAPSHOT_FRESHNESS.copy(), None
        try:
            checked_at = require_time('dataset.checkedAt', pointer['snapshot']['entry']['checkedAt'])
        except (KeyError, TypeError, ValueError):
            return None, {'status': 'refresh-required'}
        if checked_at > now:
            return None, {'status': 'refresh-required'}
        deadline = checked_at + policy['maxAgeSeconds']
        if not math.isfinite(deadline) or now >= deadline:
            return None, {'status': 'refresh-required'}
        return {**policy, 'checkedAt': checked_at, 'deadline': deadline}, None

    def _same(self, row, pointer: dict[str, Any], principal: str) -> bool:
        if not row:
            return False
        current = json.loads(row[0])
        same_generation = current['generation'] == pointer['generation']
        same_fingerprint = current['fingerprint'] == pointer['fingerprint']
        return (same_generation and same_fingerprint
                and principal in current['principals'])

    def _valid_result(self, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if not isinstance(result.get('status'), str):
            return False
        if result['status'] not in STATUSES:
            return False
        if result['status'] == 'ready':
            passages = result.get('passages')
            if not isinstance(passages, list) or not passages:
                return False
            text_fields = ('sourceId', 'path', 'contentSHA', 'reviewedText')
            for passage in passages:
                if not isinstance(passage, dict):
                    return False
                if any(not isinstance(passage.get(key), str) or not passage[key]
                       for key in text_fields):
                    return False
                start = passage.get('startLine')
                end = passage.get('endLine')
                if isinstance(start, bool) or not isinstance(start, int):
                    return False
                if isinstance(end, bool) or not isinstance(end, int):
                    return False
                if start < 1 or end < start:
                    return False
        try:
            pack(result)
        except (TypeError, ValueError):
            return False
        return True

    def _expiry(self, now: float, freshness_metadata: dict[str, Any]) -> dict[str, Any]:
        """A saved answer's expiry: the operator's optional TTL and the source-currentness
        deadline, whichever comes first; with neither, no `expires` (it lasts until its
        source changes)."""
        limits = [freshness_metadata['deadline']] if 'deadline' in freshness_metadata else []
        if self.cache_ttl_seconds is not None:
            limits.append(now + self.cache_ttl_seconds)
        return {'expires': min(limits)} if limits else {}

    def _valid_hit(self, hit: Any) -> bool:
        """Accept only the compact, reviewed answer shape stored by approve()."""
        if not isinstance(hit, dict) or not isinstance(hit.get('answer'), str) or not hit['answer']:
            return False
        try:
            if 'expires' in hit:
                require_time('cache.expires', hit['expires'])
        except ValueError:
            return False
        evidence = hit.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            return False
        for item in evidence:
            if not isinstance(item, dict):
                return False
            for field in ('sourceId', 'quote', 'path', 'contentSHA'):
                if not isinstance(item.get(field), str) or not item[field]:
                    return False
            start, end = item.get('startLine'), item.get('endLine')
            if (isinstance(start, bool) or not isinstance(start, int) or start < 1
                    or isinstance(end, bool) or not isinstance(end, int) or end < start):
                return False
        return True

    def _record_attempt(
        self, name: str, question: str, principal: str, context: str,
        freshness: dict[str, Any], status: str, trace: Any,
        pointer: dict[str, Any] | None = None,
    ) -> str:
        """Persist an immutable original retrieval attempt and return its id."""
        attempt = str(uuid.uuid4())
        body = {
            'question': question, 'context': context, 'principal': principal,
            'freshness': freshness, 'retrievalStatus': status,
            'trace': trace.get('trace', []) if isinstance(trace, dict) else [],
            **({'retrievalReason': trace['reason']}
               if isinstance(trace, dict) and isinstance(trace.get('reason'), str)
               else {}),
            'createdAt': time.time(),
        }
        generation = pointer.get('generation') if pointer else None
        fingerprint = pointer.get('fingerprint') if pointer else None
        with self.connect() as c:
            c.execute('INSERT INTO attempts VALUES (?,?,?,?,?)',
                      (attempt, name, generation, fingerprint, pack(body)))
        return attempt

    def _stored_pointer(self, name: str, principal: str) -> dict[str, Any] | None:
        """Return a stored binding only when its principal scope permits it."""
        with self.connect() as c:
            row = c.execute('SELECT body FROM pointers WHERE name=?',
                            (name,)).fetchone()
        if not row:
            return None
        pointer = json.loads(row[0])
        return pointer if principal in pointer.get('principals', []) else None

    def attempt(self, attempt_id: str, principal: str) -> dict[str, Any]:
        """Inspect an attempt using its original principal scope label."""
        require_text('attemptId', attempt_id)
        require_text('principal', principal)
        with self.connect() as c:
            row = c.execute(
                'SELECT pointer,generation,fingerprint,body FROM attempts WHERE attempt=?',
                (attempt_id,),
            ).fetchone()
        if not row:
            raise ValueError('unknown attempt')
        body = json.loads(row[3])
        if body['principal'] != principal:
            raise ValueError('unauthorized attempt')
        with self.connect() as c:
            current = c.execute('SELECT body FROM pointers WHERE name=?',
                                (row[0],)).fetchone()
        if not current:
            raise ValueError('unauthorized attempt')
        binding = json.loads(current[0])
        if (principal not in binding.get('principals', [])
                or binding.get('generation') != row[1]
                or binding.get('fingerprint') != row[2]):
            raise ValueError('unauthorized attempt')
        return {'status': 'ok', 'attemptId': attempt_id, 'pointer': row[0],
                'generation': row[1], 'fingerprint': row[2], **body}

    @staticmethod
    def _passage_id(ticket: str, passage: dict[str, Any]) -> str:
        return digest([ticket, {key: passage[key] for key in
                      ('sourceId', 'path', 'contentSHA', 'startLine', 'endLine',
                       'reviewedText')}])

    def _ticket(
        self, name: str, pointer: dict[str, Any], question: str, principal: str,
        context: str, policy: dict[str, Any], result: dict[str, Any], now: float,
        resolution: str, originating_attempt: str,
        fixed_now: bool = False,
        assistance_reason: str | None = None,
    ) -> dict[str, Any]:
        ticket = str(uuid.uuid4())
        passages = [{**p, 'evidenceId': self._passage_id(ticket, p)}
                    for p in result['passages']]
        result = {**result, 'passages': passages}
        pending = {
            'question': question, 'principal': principal, 'context': context,
            'freshness': policy, 'result': result,
            'expires': now + self.review_ttl_seconds,
            'resolution': resolution, 'originatingAttemptId': originating_attempt,
            **({'assistanceReason': assistance_reason}
               if assistance_reason is not None else {}),
        }
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT body FROM pointers WHERE name=?',
                                (name,)).fetchone()
            if not self._same(current, pointer, principal):
                raise ValueError('pointer changed while creating ticket')
            ticket_now = now if fixed_now else time.time()
            _, freshness_error = self.freshness(pointer, policy, ticket_now)
            if freshness_error:
                raise ValueError('refresh required while creating ticket')
            pending['expires'] = ticket_now + self.review_ttl_seconds
            c.execute('INSERT INTO pending VALUES (?,?,?,?,?)',
                      (ticket, name, pointer['generation'],
                       pointer['fingerprint'], pack(pending)))
        return {**result, 'approvalTicket': ticket,
                'resolution': resolution,
                'originatingAttemptId': originating_attempt}

    def open_attempt(
        self, name: str, question: str, principal: str, context: str = '',
    ) -> dict[str, Any]:
        """Record an attempt without running retrieval, for assist to cite a file the
        caller's own ranking already chose. assist still checks the reviewed preparation."""
        require_text('pointer', name)
        require_text('question', question)
        require_text('principal', principal)
        require_text('context', context, allow_empty=True)
        policy = normalize_freshness(None)
        pointer, error = self.pointer(name, principal)
        if error:
            return error
        _, error = self.freshness(pointer, policy, time.time())
        if error:
            return error
        attempt = self._record_attempt(name, question, principal, context, policy,
                                       'caller-ranked', {'trace': []}, pointer)
        return {'status': 'ok', 'attemptId': attempt}

    def assist(
        self, attempt_id: str, principal: str, reason: str,
        references: list[dict[str, Any]], now: float | None = None,
    ) -> dict[str, Any]:
        """Create a review ticket from referenced, registered preparations."""
        require_text('reason', reason)
        inspected = self.attempt(attempt_id, principal)
        if inspected['retrievalStatus'] not in {'ready', 'no-match', 'refused', 'caller-ranked'}:
            raise ValueError('attempt is not eligible for assistance')
        if (not isinstance(references, list) or not references or
                len(references) > 20 or any(not isinstance(r, dict) for r in references)):
            raise ValueError('references must contain 1 to 20 objects')
        name = inspected['pointer']
        pointer, error = self.pointer(name, principal)
        if error:
            raise ValueError(error['status'])
        if (pointer['generation'] != inspected['generation'] or
                pointer['fingerprint'] != inspected['fingerprint']):
            raise ValueError('attempt pointer binding is stale')
        policy = normalize_freshness(inspected['freshness'])
        supplied_now = now is not None
        check_now = time.time() if now is None else require_time('now', now)
        freshness_metadata, error = self.freshness(pointer, policy, check_now)
        if error:
            raise ValueError(error['status'])
        manifest = self._manifest(pointer['snapshot']['entry'])
        sources = {source['id']: source for source in manifest['sources']}
        selected: list[dict[str, Any]] = []
        seen = set()
        for ref in references:
            if set(ref) != {'sourceId', 'startLine', 'endLine'}:
                raise ValueError('invalid reference fields')
            source_id = require_text('reference.sourceId', ref['sourceId'])
            start, end = ref['startLine'], ref['endLine']
            if (isinstance(start, bool) or not isinstance(start, int) or start < 1 or
                    isinstance(end, bool) or not isinstance(end, int) or end < start):
                raise ValueError('invalid reference bounds')
            source = sources.get(source_id)
            if not source:
                raise ValueError('reference is outside registered sources')
            try:
                line_count = len(Path(source['path']).read_text().splitlines())
            except (OSError, UnicodeError) as exc:
                raise ValueError('registered source is unreadable') from exc
            if end > line_count:
                raise ValueError('reference bounds exceed registered source')
            try:
                matches = [p for p in manifest['preparations']
                           if p.get('sourceId') == source_id
                           and p.get('status') == 'reviewed'
                           and p.get('policy') == 'reviewed'
                           and p.get('contentSHA') == source['contentSHA']
                           and not isinstance(p.get('startLine'), bool)
                           and isinstance(p.get('startLine'), int)
                           and not isinstance(p.get('endLine'), bool)
                           and isinstance(p.get('endLine'), int)
                           and p['startLine'] <= end and p['endLine'] >= start]
            except (KeyError, TypeError) as exc:
                raise ValueError('invalid reviewed preparation') from exc
            if not matches:
                raise ValueError('reference does not resolve to reviewed preparation')
            for prep in matches:
                identity = (source_id, prep['startLine'], prep['endLine'], prep['contentSHA'])
                if identity not in seen:
                    seen.add(identity)
                    selected.append({
                        'sourceId': source_id, 'path': source['path'],
                        'contentSHA': prep['contentSHA'],
                        'startLine': prep['startLine'], 'endLine': prep['endLine'],
                        'reviewedText': prep['reviewedText'],
                    })
        if len(selected) > 20 or sum(len(p['reviewedText']) for p in selected) > 60000:
            raise ValueError('assisted evidence exceeds limits')
        # Recheck the complete binding after reading and selecting preparations.
        current, error = self.pointer(name, principal)
        if (error or current['generation'] != pointer['generation'] or
                current['fingerprint'] != pointer['fingerprint']):
            raise ValueError('attempt pointer binding is stale')
        final_now = check_now if supplied_now else time.time()
        _, error = self.freshness(current, policy, final_now)
        if error:
            raise ValueError(error['status'])
        result = {'status': 'ready', 'passages': selected}
        if not self._valid_result(result):
            raise ValueError('invalid reviewed preparation')
        response = self._ticket(
            name, pointer, inspected['question'], principal, inspected['context'],
            policy, result, final_now, 'agent-assisted', attempt_id, supplied_now,
            reason)
        return {**response, 'freshness': freshness_metadata}

    def sources(
        self, name: str, principal: str, offset: int = 0, limit: int = 25,
    ) -> dict[str, Any]:
        """List the registered reviewed sources available through a pointer."""
        if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or
                isinstance(limit, bool) or not isinstance(limit, int)
                or limit < 1 or limit > 100):
            raise ValueError('offset must be nonnegative and limit must be 1 to 100')
        pointer, error = self.pointer(name, principal)
        if error:
            return error
        manifest = self._manifest(pointer['snapshot']['entry'])
        rows = []
        for source in manifest['sources']:
            try:
                line_count = len(Path(source['path']).read_text().splitlines())
            except (OSError, UnicodeError):
                return {'status': 'preparation-required'}
            rows.append({
                'sourceId': source['id'], 'contentSHA': source['contentSHA'],
                'path': source['path'],
                **({'originalPath': source['path'], 'upstreamPath': source['originalPath']}
                   if source.get('viewTransform') else
                   ({'originalPath': source['originalPath']} if source.get('originalPath') else {})),
                'description': source.get('description', ''),
                'lineCount': line_count,
            })
        page = rows[offset:offset + limit]
        result = {'status': 'ok', 'sources': page, 'offset': offset,
                  'limit': limit, 'total': len(rows)}
        if offset + len(page) < len(rows):
            result['nextOffset'] = offset + len(page)
        return result

    def navigate(
        self, name: str, principal: str, question: str, limits: Any = None,
        last_good: bool = False,
    ) -> dict[str, Any]:
        """Return source candidates only after checking pointer state around navigation.

        last_good=True routes a stale pointer on the catalog it was registered with instead
        of refusing it: the result carries 'stale' (its status plus the files changed or
        missing since), and a missing file is never a candidate."""
        begun = self._navigate_begin(name, principal, question, limits, last_good)
        if 'status' in begun:
            return begun
        if self.navigate_provider is None:
            return {'status': 'error', 'reason': 'Navigation is unavailable.'}
        result = self.navigate_provider(question, begun['catalog'], limits)
        return self._navigate_end(name, principal, begun, result)

    def navigate_many(
        self, names: list[str], principal: str, question: str, limits: Any = None,
        last_good: bool = False,
    ) -> dict[str, Any]:
        """navigate() for several pointers whose Jev questions share provider calls.
        Each pointer gets its own result, exactly as navigate() would return it."""
        if (not isinstance(names, list) or not names or len(names) > 200
                or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names)):
            raise ValueError('pointers must be a list of 1 to 200 distinct names')
        begun = {name: self._navigate_begin(name, principal, question, limits, last_good) for name in names}
        ready = [name for name in names if 'status' not in begun[name]]
        results = {name: begun[name] for name in names if name not in ready}
        if ready:
            if self.navigate_many_provider is None:
                raw = [self.navigate_provider(question, begun[n]['catalog'], limits)
                       if self.navigate_provider else None for n in ready]
            else:
                raw = self.navigate_many_provider(question, [begun[n]['catalog'] for n in ready], limits)
            if isinstance(raw, dict):  # the whole batch failed: every pointer gets that error
                raw = [raw] * len(ready)
            if not isinstance(raw, list) or len(raw) != len(ready):
                raw = [None] * len(ready)
            for name, result in zip(ready, raw):
                results[name] = (self._navigate_end(name, principal, begun[name], result) if result is not None
                                 else {'status': 'error', 'reason': 'Navigation is unavailable.'})
        return {'status': 'ok', 'results': results}

    def _navigate_begin(
        self, name: str, principal: str, question: str, limits: Any, last_good: bool = False,
    ) -> dict[str, Any]:
        """The pointer check and catalog for one navigation, or its error status."""
        require_text('pointer', name)
        require_text('principal', principal)
        require_text('question', question)
        if limits is not None and not isinstance(limits, dict):
            raise ValueError('limits must be an object')
        if not isinstance(last_good, bool):
            raise ValueError('lastGood must be a boolean')
        pointer, error = self.pointer(name, principal)
        stale = None
        if error:
            if not (last_good and error['status'] == 'preparation-required'):
                return error
            pointer, stale = self._last_good(name, principal)
            if pointer is None:
                return error
        try:
            manifest = self._manifest(pointer['snapshot']['entry'])
        except (OSError, ValueError, KeyError, TypeError):
            if stale is None:
                raise
            # Re-prepared since registration: the snapshot still holds its reviewed sources.
            manifest = {'sources': pointer['snapshot'].get('sources') or []}
        sources = {source['id']: source for source in manifest['sources']}
        catalog = manifest.get('catalog')
        if catalog is None:
            nodes = [{'id': 'root', 'label': 'Sources',
                      'description': 'Reviewed connector sources',
                      'children': ['source:' + digest(source_id)[:24]
                                   for source_id in sorted(sources)]}]
            nodes.extend({'id': 'source:' + digest(source_id)[:24],
                          'label': ('Reviewed source ' + digest(source_id)[:12]
                                    if source.get('viewTransform') else
                                    Path(source.get('originalPath', source['path'])).name),
                          'description': source.get('description', ''),
                          'sourceId': source_id}
                         for source_id, source in sorted(sources.items()))
            catalog = {'version': 1, 'structure': 'flat-files',
                       'rootId': 'root', 'nodes': nodes}
        return {'pointer': pointer, 'sources': sources, 'catalog': catalog, 'stale': stale}

    def _navigate_end(
        self, name: str, principal: str, begun: dict[str, Any], result: Any,
    ) -> dict[str, Any]:
        """Validate one navigation result against its catalog and re-check the pointer."""
        pointer, sources, catalog = begun['pointer'], begun['sources'], begun['catalog']
        stale = begun.get('stale')
        if (isinstance(result, dict) and result.get('status') == 'error'
                and isinstance(result.get('reason'), str) and result['reason']):
            return {'status': 'error', 'reason': result['reason']}
        if (not isinstance(result, dict)
                or result.get('status') not in {'candidates', 'no-candidates',
                                                'budget-exhausted'}
                or not isinstance(result.get('candidates'), list)
                or isinstance(result.get('calls'), bool)
                or not isinstance(result.get('calls'), int)
                or result['calls'] < 0
                or not isinstance(result.get('trace'), list)
                or result.get('complete') is not False
                or not isinstance(result.get('message'), str)):
            return {'status': 'error', 'reason': 'Navigation returned invalid output.'}
        mapped, keys = [], []
        catalog_nodes = {node.get('id'): node for node in catalog.get('nodes', [])
                         if isinstance(node, dict) and isinstance(node.get('id'), str)}
        seen_candidates = set()
        for candidate in result['candidates']:
            candidate_path = candidate.get('path') if isinstance(candidate, dict) else None
            candidate_node = candidate.get('nodeId') if isinstance(candidate, dict) else None
            source_id = candidate.get('sourceId') if isinstance(candidate, dict) else None
            valid_path = (isinstance(candidate_path, list) and candidate_path
                          and candidate_path[0] == catalog.get('rootId')
                          and candidate_path[-1] == candidate_node)
            if valid_path:
                valid_path = all(
                    child in catalog_nodes.get(parent, {}).get('children', [])
                    for parent, child in zip(candidate_path, candidate_path[1:]))
            leaf = catalog_nodes.get(candidate_node, {})
            if (not isinstance(candidate, dict)
                    or set(candidate) != {'sourceId', 'nodeId', 'path', 'score'}
                    or source_id not in sources
                    or not isinstance(candidate_node, str)
                    or candidate_node in seen_candidates
                    or leaf.get('sourceId') != source_id
                    or not valid_path
                    or any(not isinstance(node, str) for node in candidate['path'])
                    or isinstance(candidate.get('score'), bool)
                    or not isinstance(candidate.get('score'), (int, float))
                    or not math.isfinite(candidate['score'])
                    or not 0 <= candidate['score'] <= 1):
                return {'status': 'error', 'reason': 'Navigation returned invalid output.'}
            seen_candidates.add(candidate_node)
            source = sources[candidate['sourceId']]
            keys.append(source.get('originalPath', source['path']))
            mapped.append({**candidate, 'originalPath': source['path'] if source.get('viewTransform') else source.get('originalPath', source['path']),
                           **({'upstreamPath': source['originalPath']} if source.get('viewTransform') else {}),
                           'contentSHA': source['contentSHA'],
                           'description': source.get('description', '')})
        if stale:
            # What changed is taken again after the provider call: a file edited or deleted
            # while Jev routed must still count as changed or missing.
            after, stale = self._last_good(name, principal)
            if after is None:
                return {'status': 'access-denied'}
        else:
            after, error = self.pointer(name, principal)
            if error:
                return error
        if (after['generation'] != pointer['generation']
                or after['fingerprint'] != pointer['fingerprint']):
            return {'status': 'pointer-changed'}
        if stale:
            # A file deleted since registration has nothing left to read.
            mapped = [c for c, key in zip(mapped, keys) if key not in stale['missing']]
            status = result['status']
            if status == 'candidates' and not mapped:
                status = 'no-candidates'
            return {**result, 'status': status, 'candidates': mapped, 'stale': stale}
        return {**result, 'candidates': mapped}

    def _cache_lookup(
        self, pointer: dict[str, Any], name: str, principal: str, question: str,
        context: str, policy: dict[str, Any], now: float, supplied_now: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Validate and return a verified cache hit for an already-fetched pointer.

        This is the one hit-validation path: request key derivation, generation/
        fingerprint rebinding against the live row, the freshness policy check, and
        _valid_hit(). search() and cached() both call this so a cache hit means the
        same thing everywhere. Returns (hit, None) on a verified hit, (None, error)
        if the live pointer binding disagrees with the caller's view, or (None, None)
        on a plain miss. Never touches retrieve() or navigate_provider().
        """
        key = self.key(pointer, question, principal, context, policy)
        with self.connect() as c:
            c.execute('BEGIN')
            hitrow = c.execute(
                'SELECT body,generation,fingerprint FROM cache WHERE k=?',
                (key,),
            ).fetchone()
            current = c.execute(
                'SELECT body FROM pointers WHERE name=?', (name,)
            ).fetchone()
            if not current:
                return None, {'status': 'unknown-pointer'}
            current_pointer = json.loads(current[0])
            if principal not in current_pointer['principals']:
                return None, {'status': 'access-denied'}
            if current_pointer['generation'] != pointer['generation']:
                return None, {'status': 'pointer-changed'}
            if hitrow:
                try:
                    hit = json.loads(hitrow[0])
                    binding = (pointer['generation'], pointer['fingerprint'])
                    hit_now = time.time() if not supplied_now else now
                    freshness_metadata, error = self.freshness(pointer, policy, hit_now)
                    if error:
                        return None, error
                    if (hitrow[1:] == binding
                            and self._valid_hit(hit) and hit.get('expires', float('inf')) > hit_now):
                        return {
                            'status': 'verified-cache-hit',
                            'answer': hit['answer'],
                            'evidence': hit['evidence'],
                            'freshness': freshness_metadata,
                            'resolution': hit.get('resolution', 'retrieval'),
                            **({'originatingAttemptId': hit['originatingAttemptId']}
                               if hit.get('originatingAttemptId') else {}),
                        }, None
                except (ValueError, KeyError, TypeError):
                    pass
        return None, None

    def search(
        self,
        name: str,
        question: str,
        principal: str,
        context: str = '',
        now: float | None = None,
        freshness: Any = None,
    ) -> dict[str, Any]:
        """Return a verified hit or a fresh result with a review ticket."""
        require_text('pointer', name)
        require_text('question', question)
        require_text('principal', principal)
        require_text('context', context, allow_empty=True)
        policy = normalize_freshness(freshness)
        supplied_now = now is not None
        now = time.time() if now is None else require_time('now', now)
        pointer, error = self.pointer(name, principal)
        if error:
            stored = self._stored_pointer(name, principal)
            attempt = self._record_attempt(name, question, principal, context,
                                           policy, error['status'], error, stored)
            return {**error, 'attemptId': attempt}
        # Pointer validation reads multiple files and can itself cross a deadline.
        checked_now = time.time() if not supplied_now else now
        freshness_metadata, error = self.freshness(pointer, policy, checked_now)
        if error:
            attempt = self._record_attempt(name, question, principal, context,
                                           policy, error['status'], error, pointer)
            return {**error, 'attemptId': attempt}
        hit, error = self._cache_lookup(pointer, name, principal, question, context, policy, now, supplied_now)
        if error:
            return error
        if hit:
            return hit
        provider_now = time.time() if not supplied_now else now
        freshness_metadata, error = self.freshness(pointer, policy, provider_now)
        if error:
            attempt = self._record_attempt(name, question, principal, context,
                                           policy, error['status'], error, pointer)
            return {**error, 'attemptId': attempt}
        result = self.retrieve(pointer['dataset'], question)
        if not self._valid_result(result):
            result = {'status': 'error', 'reason': 'invalid-retrieval-result'}
            attempt = self._record_attempt(name, question, principal, context,
                                           policy, result['status'], result, pointer)
            return {**result, 'attemptId': attempt}
        attempt = self._record_attempt(name, question, principal, context,
                                       policy, result['status'], result, pointer)
        after, error = self.pointer(name, principal)
        if error:
            return {**error, 'attemptId': attempt}
        generation_changed = after['generation'] != pointer['generation']
        fingerprint_changed = after['fingerprint'] != pointer['fingerprint']
        if generation_changed or fingerprint_changed:
            return {'status': 'pointer-changed', 'attemptId': attempt}
        # Retrieval may have taken long enough for a currentness deadline to pass.
        after_now = time.time() if not supplied_now else now
        freshness_metadata, error = self.freshness(after, policy, after_now)
        if error:
            return {**error, 'attemptId': attempt}
        if result['status'] != 'ready':
            return {**result, 'attemptId': attempt}
        try:
            response = self._ticket(name, pointer, question, principal, context,
                                    policy, result, after_now, 'retrieval', attempt,
                                    supplied_now)
        except ValueError as exc:
            if str(exc) == 'refresh required while creating ticket':
                return {'status': 'refresh-required', 'attemptId': attempt}
            return {'status': 'pointer-changed', 'attemptId': attempt}
        return {**response, 'attemptId': attempt, 'freshness': freshness_metadata}

    def _visible_pointers(self, principal: str) -> list[str]:
        """Names of registered pointers this principal is included in, name-sorted."""
        with self.connect() as c:
            rows = c.execute('SELECT name, body FROM pointers ORDER BY name').fetchall()
        return [name for name, body in rows
                if principal in json.loads(body).get('principals', [])]

    def cached(
        self, principal: str, question: str, pointer: str | None = None, context: str = '',
    ) -> dict[str, Any]:
        """Check for an already-approved answer, making zero provider calls.

        With no pointer, checks every pointer this principal can see. Returns the
        same shape as a search() verified-cache-hit when an approved answer exists
        -- via the shared _cache_lookup() validation, so scope, generation and
        source freshness are checked exactly as search() checks them -- else
        {'status': 'cache-miss', 'checked': [pointer, ...]}. Never calls retrieve()
        or navigate_provider(); a stale, denied or unknown pointer is simply
        excluded from the hit and recorded as checked.
        """
        require_text('principal', principal)
        require_text('question', question)
        require_text('context', context, allow_empty=True)
        policy = normalize_freshness(None)
        now = time.time()
        names = [pointer] if pointer else self._visible_pointers(principal)
        checked = []
        with self.connect() as c:
            with_rows = {r[0] for r in c.execute('SELECT DISTINCT pointer FROM cache')}
        for name in names:
            if name not in with_rows:
                # No cache row for this pointer: a hit is impossible, so skip
                # the snapshot/re-hash in self.pointer().
                checked.append(name)
                continue
            bound, error = self.pointer(name, principal)
            if error:
                checked.append(name)
                continue
            hit, error = self._cache_lookup(bound, name, principal, question, context, policy, now, False)
            if hit:
                return hit
            checked.append(name)
        return {'status': 'cache-miss', 'checked': checked}

    def forget(self, principal: str, question: str, context: str = '') -> dict[str, Any]:
        """Un-save this principal's approved answer for this exact question, on every
        pointer it can see. Only the cache row goes; the pointer and its data stay."""
        require_text('principal', principal)
        require_text('question', question)
        require_text('context', context, allow_empty=True)
        policy = normalize_freshness(None)
        removed = []
        for name in self._visible_pointers(principal):
            bound, error = self.pointer(name, principal)
            if error:
                continue
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                if c.execute('DELETE FROM cache WHERE k=? AND pointer=?',
                             (self.key(bound, question, principal, context, policy), name)).rowcount:
                    removed.append(name)
        return {'status': 'forgotten' if removed else 'not-cached', 'pointers': removed}

    def approve(
        self,
        ticket: str,
        principal: str,
        answer: str,
        evidence: list[dict[str, Any]],
        approved: bool = False,
        now: float | None = None,
    ) -> None:
        """Validate reviewed evidence and cache an explicitly approved answer."""
        require_text('ticket', ticket)
        require_text('principal', principal)
        require_text('answer', answer)
        supplied_now = now is not None
        now = time.time() if now is None else require_time('now', now)
        invalid_evidence = (
            not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(item, dict) for item in evidence)
        )
        if approved is not True or invalid_evidence:
            raise ValueError('explicit review required')
        with self.connect() as c:
            row = c.execute(
                'SELECT pointer,generation,fingerprint,body FROM pending WHERE ticket=?',
                (ticket,),
            ).fetchone()
        if not row:
            raise ValueError('unknown or consumed ticket')
        name, generation, fingerprint, body = row
        pending = json.loads(body)
        pointer, error = self.pointer(name, principal)
        stale_binding = (
            error
            or pointer['generation'] != generation
            or pointer['fingerprint'] != fingerprint
            or principal != pending['principal']
            or now >= pending['expires']
        )
        if stale_binding:
            raise ValueError('stale or unauthorized review')
        policy = normalize_freshness(pending.get('freshness'))
        freshness_metadata, freshness_error = self.freshness(pointer, policy, now)
        if freshness_error:
            raise ValueError('refresh required before approval')
        manifest = self._manifest(pointer['snapshot']['entry'])
        sources = {s['id']: s for s in manifest['sources']}
        verified = []
        for item in evidence:
            evidence_id = item.get('evidenceId')
            if evidence_id is not None:
                if set(item) != {'evidenceId'} or not isinstance(evidence_id, str):
                    raise ValueError('invalid evidence id')
                returned = [p for p in pending['result'].get('passages', [])
                            if p.get('evidenceId') == evidence_id]
                if len(returned) != 1:
                    raise ValueError('evidence id not in ticket')
                item = {'sourceId': returned[0]['sourceId'],
                        'quote': returned[0]['reviewedText']}
            try:
                quote = item['quote']
            except KeyError as exc:
                raise ValueError('invalid evidence') from exc
            matches = []
            for passage in pending['result'].get('passages', []):
                wrong_source = passage['sourceId'] != item['sourceId']
                if wrong_source or not quote or quote not in passage['reviewedText']:
                    continue
                source = sources.get(item['sourceId'])
                wrong_path = source and passage['path'] != source['path']
                wrong_hash = source and passage['contentSHA'] != source['contentSHA']
                if not source or wrong_path or wrong_hash:
                    continue
                matches += [
                    p for p in manifest['preparations']
                    if p['sourceId'] == item['sourceId']
                    and p['contentSHA'] == source['contentSHA']
                    and p['status'] == 'reviewed' and p['policy'] == 'reviewed'
                    and quote in p['reviewedText'] and passage['startLine'] <=
                    p['startLine'] and p['endLine'] <= passage['endLine']
                ]
            if not matches:
                raise ValueError('quote not in reviewed evidence')
            prep = matches[0]
            verified.append({
                **item, 'path': sources[item['sourceId']]['path'],
                'contentSHA': prep['contentSHA'],
                'startLine': prep['startLine'],
                'endLine': prep['endLine']
            })
        # This closes detected-change races before commit. The local filesystem itself
        # provides no multi-file snapshot isolation, so deployment must retain trusted storage.
        try:
            current_snapshot = self.snapshot(pointer['dataset'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('stale review evidence') from exc
        if digest(current_snapshot) != fingerprint:
            raise ValueError('stale review evidence')
        # Re-sample time because evidence verification can take an unbounded time.
        approval_now = time.time() if not supplied_now else now
        if approval_now >= pending['expires']:
            raise ValueError('stale or unauthorized review')
        freshness_metadata, freshness_error = self.freshness(pointer, policy, approval_now)
        if freshness_error:
            raise ValueError('refresh required before approval')
        hit = {
            'answer': answer,
            'evidence': verified,
            'resolution': pending.get('resolution', 'retrieval'),
            'originatingAttemptId': pending.get('originatingAttemptId'),
            **({'assistanceReason': pending['assistanceReason']}
               if pending.get('assistanceReason') else {}),
            **self._expiry(approval_now, freshness_metadata)
        }
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT body FROM pointers WHERE name=?',
                                (name, )).fetchone()
            if not self._same(current, pointer, principal):
                raise ValueError('pointer changed during approval')
            # Waiting for the write lock can outlast a ticket or source deadline.
            commit_now = time.time() if not supplied_now else now
            if commit_now >= pending['expires']:
                raise ValueError('stale or unauthorized review')
            freshness_metadata, freshness_error = self.freshness(pointer, policy, commit_now)
            if freshness_error:
                raise ValueError('refresh required before approval')
            hit.pop('expires', None)
            hit.update(self._expiry(commit_now, freshness_metadata))
            if c.execute(
                    'DELETE FROM pending WHERE ticket=? AND generation=? AND fingerprint=?',
                (ticket, generation, fingerprint)).rowcount != 1:
                raise ValueError('consumed ticket')
            c.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)',
                      (self.key(pointer, pending['question'], principal,
                                pending['context'], policy), name, generation,
                       fingerprint, pack(hit)))
