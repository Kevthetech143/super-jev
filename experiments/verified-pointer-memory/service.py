"""Trusted-local verified pointer/cache experiment."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "2"
STATUSES = {"ready", "no-match", "preparation-required", "refused", "error"}


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


class Service:
    """Store verified dataset pointers, review tickets, and approved results."""

    def __init__(
        self,
        db: str | Path,
        registry: str | Path,
        retrieve: Callable[[str, str], dict[str, Any]],
        *,
        cache_ttl_seconds: float = 86400,
        review_ttl_seconds: float = 600,
    ) -> None:
        ttl_values = (
            ("cache_ttl_seconds", cache_ttl_seconds),
            ("review_ttl_seconds", review_ttl_seconds),
        )
        for label, value in ttl_values:
            invalid_type = isinstance(value, bool) or not isinstance(value, (int, float))
            if invalid_type or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive finite number")
        self.db = str(db)
        self.registry = Path(registry)
        self.retrieve = retrieve
        self.cache_ttl_seconds = float(cache_ttl_seconds)
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
        entry = json.loads(self.registry.read_bytes())['datasets'][dataset]
        manifest = self._manifest(entry)
        for original in entry['originals']:
            if sha(original['path']) != original['sha256']:
                raise ValueError('stale original')
        for source in manifest['sources']:
            if sha(source['path']) != source['contentSHA']:
                raise ValueError('stale source')
        return {'entry': entry, 'sources': manifest['sources']}

    def register(self, name: str, dataset: str, principals: list[str]) -> None:
        """Register a pointer and clear data from its previous generation."""
        require_text('pointer', name)
        require_text('dataset', dataset)
        if not isinstance(principals, list) or any(
                not isinstance(p, str) or not p for p in principals):
            raise ValueError('principals must be a list of nonempty strings')
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
            c.execute('DELETE FROM cache WHERE pointer=?', (name, ))
            c.execute('DELETE FROM pending WHERE pointer=?', (name, ))
            c.execute('INSERT OR REPLACE INTO pointers VALUES (?,?)',
                      (name, pack(row)))

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

    def key(
        self, pointer: dict[str, Any], question: str, principal: str, context: str
    ) -> str:
        """Build a request key scoped to an immutable generation."""
        return digest([pointer['generation'], question, principal, context])

    def _same(self, row, pointer: dict[str, Any], principal: str) -> bool:
        if not row:
            return False
        current = json.loads(row[0])
        same_generation = current['generation'] == pointer['generation']
        return same_generation and principal in current['principals']

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

    def search(
        self,
        name: str,
        question: str,
        principal: str,
        context: str = '',
        now: float | None = None,
    ) -> dict[str, Any]:
        """Return a verified hit or a fresh result with a review ticket."""
        require_text('pointer', name)
        require_text('question', question)
        require_text('principal', principal)
        require_text('context', context, allow_empty=True)
        now = time.time() if now is None else now
        pointer, error = self.pointer(name, principal)
        if error:
            return error
        key = self.key(pointer, question, principal, context)
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
                return {'status': 'unknown-pointer'}
            current_pointer = json.loads(current[0])
            if principal not in current_pointer['principals']:
                return {'status': 'access-denied'}
            if current_pointer['generation'] != pointer['generation']:
                return {'status': 'pointer-changed'}
            if hitrow:
                try:
                    hit = json.loads(hitrow[0])
                    binding = (pointer['generation'], pointer['fingerprint'])
                    if hitrow[1:] == binding and hit['expires'] > now:
                        return {
                            'status': 'verified-cache-hit',
                            'answer': hit['answer'],
                            'evidence': hit['evidence']
                        }
                except (ValueError, KeyError, TypeError):
                    pass
        result = self.retrieve(pointer['dataset'], question)
        if not self._valid_result(result):
            return {'status': 'error', 'reason': 'invalid-retrieval-result'}
        after, error = self.pointer(name, principal)
        if error:
            return error
        generation_changed = after['generation'] != pointer['generation']
        fingerprint_changed = after['fingerprint'] != pointer['fingerprint']
        if generation_changed or fingerprint_changed:
            return {'status': 'pointer-changed'}
        if result['status'] != 'ready':
            return result
        ticket = str(uuid.uuid4())
        pending = {
            'question': question,
            'principal': principal,
            'context': context,
            'result': result,
            'expires': now + self.review_ttl_seconds,
        }
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute(
                'SELECT body FROM pointers WHERE name=?', (name,)
            ).fetchone()
            if not self._same(current, pointer, principal):
                return {'status': 'pointer-changed'}
            c.execute('INSERT INTO pending VALUES (?,?,?,?,?)',
                      (ticket, name, pointer['generation'],
                       pointer['fingerprint'], pack(pending)))
        return {**result, 'approvalTicket': ticket}

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
        now = time.time() if now is None else now
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
        manifest = self._manifest(pointer['snapshot']['entry'])
        sources = {s['id']: s for s in manifest['sources']}
        verified = []
        for item in evidence:
            quote = item['quote']
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
        hit = {
            'answer': answer,
            'evidence': verified,
            'expires': now + self.cache_ttl_seconds
        }
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT body FROM pointers WHERE name=?',
                                (name, )).fetchone()
            if not self._same(current, pointer, principal):
                raise ValueError('pointer changed during approval')
            if c.execute(
                    'DELETE FROM pending WHERE ticket=? AND generation=? AND fingerprint=?',
                (ticket, generation, fingerprint)).rowcount != 1:
                raise ValueError('consumed ticket')
            c.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)',
                      (self.key(pointer, pending['question'], principal,
                                pending['context']), name, generation,
                       fingerprint, pack(hit)))
