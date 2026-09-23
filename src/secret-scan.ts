/** Node twin of Python has_secret / payload_has_secret (skills/super-jev/prepare_bulk.py).
 * Both load the one pattern source, skills/super-jev/secret_patterns.json. */
import { readFileSync } from 'node:fs';

const PAT = JSON.parse(readFileSync(new URL('../skills/super-jev/secret_patterns.json', import.meta.url), 'utf8'));
// No 'u' flag: \w, \d and \b stay ASCII-only, the twin of Python's re.ASCII. The text is ASCII after normalizing.
const CARD = new RegExp(PAT.card);
const WORD = new RegExp(PAT.word, 'i');
const TOKEN = new RegExp(PAT.token, 'i');
const GENERIC = new RegExp(PAT.generic, 'gi');
const ISO_DATE = new RegExp(PAT.iso_date, 'g');
const URL_RE = new RegExp(PAT.url, 'g');

function entropy(s: string): number {
  let h = 0;
  for (const c of new Set(s)) { const p = s.split(c).length - 1; h -= (p / s.length) * Math.log2(p / s.length); }
  return h;
}

const NON_ASCII = /[^\x00-\x7f]/gu;
const CTRL = /[\x00-\x09\x0b-\x1f\x7f]/g;
const LETTER = /^[\p{L}\p{M}\p{Cn}]$/u;
const DIGIT = /^\p{Nd}$/u;

const FOLD = new Map<string, string>();
const foldOne = (c: string) => c.charCodeAt(0) < 0x80 ? c : LETTER.test(c) ? 'x' : DIGIT.test(c) ? '0' : ' ';
function foldChar(c: string): string {
  let f = FOLD.get(c);
  if (f === undefined) FOLD.set(c, f = Array.from(c === 'ı' ? c : c.toUpperCase().toLowerCase(), foldOne).join(''));
  return f;
}

/** Twin of Python normalize_for_scan: NFKC, casefold, every control or whitespace char but newline to a
 * space, any remaining non-ASCII letter/mark/unassigned to "x" and digit to "0", anything else non-ASCII to a space.
 * JS has no casefold; upper-then-lower of each non-ASCII char matches it (dotless ı excepted) for every code point
 * both Unicode versions assign - checked once over all code points against Python 3.12. */
export function normalizeForScan(text: string): string {
  return text.normalize('NFKC').toLowerCase().replace(NON_ASCII, foldChar).replace(CTRL, ' ');
}

export function hasSecret(text: string): boolean {
  text = normalizeForScan(text);
  if (CARD.test(text.replace(URL_RE, ' ').replace(ISO_DATE, ' '))) return true;
  if (WORD.test(text) || TOKEN.test(text)) return true;
  for (const m of text.matchAll(GENERIC)) {
    const v = m[4];
    if (v && entropy(v) >= 3.5 && /\d/.test(v) && /[A-Za-z]/.test(v)) return true;
  }
  return false;
}

/** hasSecret over every string (keys and values) inside a request payload. */
export function payloadHasSecret(obj: unknown): boolean {
  if (typeof obj === 'string') return hasSecret(obj);
  if (Array.isArray(obj)) return obj.some(payloadHasSecret);
  if (obj && typeof obj === 'object') return Object.entries(obj).some(([k, v]) => hasSecret(k) || payloadHasSecret(v));
  return false;
}
