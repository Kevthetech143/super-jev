/**
 * EVIDENCE GUARD: the one place every door asks "may I open this path?" and
 * "is this text safe to ship?" before it opens a path or lets text into an
 * evidence pack that goes to the judge.
 *
 * Ported from the read guard in
 * the original evidence-atoms prototype (section 0,
 * added 2026-09-18 for AUDIT.md findings S1-S7: most verification doors had no
 * read blocklist and the one that did was name-only, so a grep or a file read
 * rooted anywhere near $HOME could put a private line into evidence that is
 * then sent off-box to the judge).
 *
 * Two rules, both fail-closed:
 *   isBlockedPath(p)  -> true means NEVER open it. The caller skips the path
 *                        entirely and states "skipped: blocked path" as a
 *                        fact (blockedPathFact), so a blocked path is
 *                        UNPROVABLE, never FALSE.
 *   redact(text)      -> the same text with secret-shaped spans replaced by
 *                        [REDACTED:kind]. Every door runs it over every
 *                        evidence block before it is packed or sent.
 *
 * A pure-Python mirror of this file lives at skills/super-jev/superjev.py
 * (search "EVIDENCE GUARD"), for the Stop hook and the verify fallback's
 * local gatherer, which run in Python. test/enhance/fixtures/evidence-guard-
 * cases.json is the one fixture both sides run, so the two languages cannot
 * silently drift apart.
 */

// ------------------------------------------------------------- blocked paths

export const BLOCKED_PATH_PATTERNS: readonly string[] = [
  '(^|/)logins\\.md$',                 // the fleet login vault
  '(^|/)[^/]*-secret\\.md(/|$)',       // ~/agents/global/tools/*-secret.md
  '(^|/)[^/]*secret[^/]*(/|$)',        // any path segment naming a secret
  '(^|/)\\.env(/|$)',                  // .env
  '(^|/)\\.env\\.[^/]*(/|$)',          // .env.local, .env.production
  '(^|/)profile(/|$)',                 // ~/agents/global/profile/
  '(^|/)documents(/|$)',               // ~/agents/global/documents/
  '(^|/)\\.config/pw-[^/]*',           // playwright session profiles
  '(^|/)[^/]*cookies[^/]*(/|$)',       // cookies.sqlite, Cookies
  '(^|/)[^/]*\\.pem(/|$)',
  '(^|/)[^/]*\\.key(/|$)',
  '(^|/)id_rsa[^/]*(/|$)',
  '(^|/)[^/]*tokens?[^/]*(/|$)',       // token / tokens / *token*.json
  '(^|/)[^/]*credentials?[^/]*(/|$)',  // ~/.aws/credentials, credentials.json
];
const BLOCKED_PATH_RX = new RegExp(BLOCKED_PATH_PATTERNS.join('|'), 'i');

export const SKIPPED_FACT = 'skipped: blocked path';

function expandHome(p: string): string {
  if (p === '~') return process.env.HOME ?? p;
  if (p.startsWith('~/')) return (process.env.HOME ?? '~') + p.slice(1);
  return p;
}

/**
 * True when `p` must never be opened, read, grepped or listed. Matching is on
 * the path STRING (`~` expanded), segment-anchored, case insensitive. `allow`
 * is an explicit per-run override: any entry that is a prefix of, or equal
 * to, the expanded path unblocks it. Empty by default.
 */
export function isBlockedPath(p: string | undefined | null, allow: readonly string[] = []): boolean {
  if (!p) return false;
  const full = expandHome(String(p));
  for (const a0 of allow) {
    const a = expandHome(String(a0));
    if (full === a || full.startsWith(a.replace(/\/+$/, '') + '/')) return false;
  }
  return BLOCKED_PATH_RX.test(full);
}

/** The fact a gatherer returns INSTEAD of reading a blocked path. `ok` is
 * false and `exists` is null on purpose: a blocked path is UNPROVABLE, so no
 * pre-rule may read it as "missing" and hard-block a true claim. */
export function blockedPathFact(path: string, source = 'path'): Record<string, unknown> {
  return {
    ok: false, value: null, source, blocked: true,
    cmd: `(${SKIPPED_FACT}: ${path})`, exists: null, path, full: null,
    lines: null, state: 'blocked',
  };
}

// ------------------------------------------------------------------ redactor

type RedactKind =
  | 'openai-key' | 'github-token' | 'slack-token' | 'aws-key-id'
  | 'bearer-token' | 'authorization' | 'long-hex' | 'api-key' | 'email';

// Ordered most-specific first; the first pattern that matches a span names
// the kind.
const REDACT_PATTERNS: ReadonlyArray<[RedactKind, string]> = [
  ['openai-key', '\\bsk-[A-Za-z0-9_-]{12,}'],
  ['github-token', '\\bgh[pousr]_[A-Za-z0-9]{12,}'],
  ['slack-token', '\\bxox[baprs]-[A-Za-z0-9-]{8,}'],
  ['aws-key-id', '\\bAKIA[0-9A-Z]{12,}'],
  ['bearer-token', '\\bbearer\\s+[A-Za-z0-9._-]{8,}'],
  ['authorization', '\\bauthorization\\s*:\\s*\\S+'],
  ['long-hex', '\\b[0-9a-fA-F]{65,}\\b'],
  ['api-key', '\\b[A-Za-z0-9_-]{30,}\\b'],
];
const EMAIL_PATTERN: [RedactKind, string] = ['email', '\\b[\\w.+-]+@[\\w-]+\\.[\\w.-]{2,}\\b'];

const COMPILED_REDACT: ReadonlyArray<[RedactKind, RegExp]> =
  REDACT_PATTERNS.map(([k, p]) => [k, new RegExp(p, k === 'authorization' || k === 'bearer-token' ? 'gi' : 'g')]);
const COMPILED_EMAIL: [RedactKind, RegExp] = [EMAIL_PATTERN[0], new RegExp(EMAIL_PATTERN[1], 'g')];

const HEX_ONLY_RX = /^[0-9a-fA-F]+$/;

/** A git hash (7-40 hex), an md5 (32) or a sha256 (64) is EVIDENCE, not a
 * secret. Pure hex up to 64 characters is never redacted; 65+ is
 * (long-hex). */
function looksLikeDigest(s: string): boolean {
  return HEX_ONLY_RX.test(s) && s.length <= 64;
}

/** A long alnum/-/_ blob split into short words, none mixing case with
 * digits the way a key does, is an ordinary identifier, not a key. */
function looksLikeIdentifier(s: string): boolean {
  const parts = s.split(/[-_]/);
  if (parts.length < 2) return false;
  if (parts.some(p => p.length > 12)) return false;
  for (const p of parts) {
    if (/[A-Z]/.test(p) && /[0-9]/.test(p)) return false;
  }
  return true;
}

/** The generic 30+ character api-key test, with the digest/identifier
 * exemptions above. */
function isSecretBlob(s: string): boolean {
  if (s.length < 30) return false;
  if (looksLikeDigest(s)) return false;
  if (!(/[A-Za-z]/.test(s) && /[0-9]/.test(s))) return false;
  if (looksLikeIdentifier(s)) return false;
  return true;
}

/** Is this span a whole path segment, i.e. slash-delimited on both sides?
 * macOS private tmp dirs look exactly like a key; a slash-delimited blob is
 * treated as a path segment, not a credential. */
function isPathSegment(text: string, start: number, end: number): boolean {
  const before = start > 0 ? text[start - 1] : '';
  const after = end < text.length ? text[end] : '';
  return before === '/' && (after === '/' || after === '');
}

export interface RedactResult {
  text: string;
  count: number;
  byKind: Partial<Record<RedactKind, number>>;
}

/**
 * Return `text` with every secret-shaped span replaced by [REDACTED:kind],
 * plus the count of redactions made. Never throws; safe to run over a whole
 * evidence block, a rendered sentence or a JSON dump. `redactEmails` is OFF
 * by default, because an email address is often the identity a claim is
 * about (authorship, a `from` field).
 */
export function redactCounted(text: string | undefined | null, redactEmails = false): RedactResult {
  if (!text) return { text: text ?? '', count: 0, byKind: {} };
  let out = String(text);
  const byKind: Partial<Record<RedactKind, number>> = {};
  for (const [kind, rx] of COMPILED_REDACT) {
    rx.lastIndex = 0;
    out = out.replace(rx, (span: string, offset: number) => {
      if (kind === 'api-key' && isPathSegment(out, offset, offset + span.length)) return span;
      if (kind === 'api-key' && !isSecretBlob(span)) return span;
      byKind[kind] = (byKind[kind] ?? 0) + 1;
      return `[REDACTED:${kind}]`;
    });
  }
  if (redactEmails) {
    const [kind, rx] = COMPILED_EMAIL;
    rx.lastIndex = 0;
    out = out.replace(rx, () => { byKind[kind] = (byKind[kind] ?? 0) + 1; return `[REDACTED:${kind}]`; });
  }
  const count = Object.values(byKind).reduce((a, b) => a + (b ?? 0), 0);
  return { text: out, count, byKind };
}

/** Convenience form that drops the count, for call sites that only want the
 * text (matches atoms.py's `redact(text)` signature). */
export function redact(text: string | undefined | null, redactEmails = false): string {
  return redactCounted(text, redactEmails).text;
}

// -------------------------------------------------------------- explain tally

/** Running counters a door accumulates across one call, for a `guard`
 * subcommand or an `--explain` line: how many paths were skipped and how
 * many redactions were made. */
export class GuardTally {
  pathsSkipped = 0;
  redactions = 0;
  byKind: Partial<Record<RedactKind, number>> = {};

  notePath(blocked: boolean): void {
    if (blocked) this.pathsSkipped += 1;
  }

  noteRedaction(result: RedactResult): void {
    this.redactions += result.count;
    for (const [k, v] of Object.entries(result.byKind)) {
      this.byKind[k as RedactKind] = (this.byKind[k as RedactKind] ?? 0) + (v ?? 0);
    }
  }

  /** Apply redact + tally in one step, the call sites' normal path. */
  redact(text: string | undefined | null, redactEmails = false): string {
    const r = redactCounted(text, redactEmails);
    this.noteRedaction(r);
    return r.text;
  }

  /** Apply isBlockedPath + tally in one step. */
  checkPath(p: string | undefined | null, allow: readonly string[] = []): boolean {
    const blocked = isBlockedPath(p, allow);
    this.notePath(blocked);
    return blocked;
  }

  summary(): string {
    return `guard: ${this.pathsSkipped} path(s) skipped, ${this.redactions} redaction(s)`;
  }

  toJSON(): { pathsSkipped: number; redactions: number; byKind: Partial<Record<RedactKind, number>> } {
    return { pathsSkipped: this.pathsSkipped, redactions: this.redactions, byKind: this.byKind };
  }
}
