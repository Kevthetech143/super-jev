/** Node twin of Python has_secret / payload_has_secret (skills/super-jev/prepare_bulk.py).
 * Both load the one pattern source, skills/super-jev/secret_patterns.json. */
import { readFileSync } from 'node:fs';

const PAT = JSON.parse(readFileSync(new URL('../skills/super-jev/secret_patterns.json', import.meta.url), 'utf8'));
// No 'u' flag: \w, \d and \b stay ASCII-only, the twin of Python's re.ASCII. The text is ASCII after normalizing.
const TEST_CARDS = new Set<string>(PAT.test_cards);
const CARD = new RegExp(PAT.card, 'g');
const CARD_IIN = new RegExp(PAT.card_iin);
const AMEX = new RegExp(PAT.amex, 'g');
const WORD = new RegExp(PAT.word.replace('{PH}', PAT.placeholder).replaceAll('{STOP}', PAT.stop), 'i');
const TOKEN = new RegExp(PAT.token.replace('{PH}', PAT.placeholder), 'i');
const GENERIC = new RegExp(PAT.generic, 'gi');
const ISO_DATE = new RegExp(PAT.iso_date, 'g');
const URL_RE = new RegExp(PAT.url, 'g');
const TRACKING = new RegExp(PAT.tracking, 'g');

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

/** Twin of Python _real_card: Luhn-valid and not a published processor test number. */
function realCard(d: string): boolean { return luhn(d) && !TEST_CARDS.has(d); }
function luhn(digits: string): boolean {
  let total = 0;
  for (let i = 0; i < digits.length; i++) {
    let n = Number(digits[digits.length - 1 - i]) * (i % 2 ? 2 : 1);
    total += n > 9 ? n - 9 : n;
  }
  return total % 10 === 0;
}

/** Twin of Python _usps_check_ok: a TRACKING run (prefix and USPS layout already checked) of 22 or 26 digits
 * with a valid GS1 mod-10 check digit. */
function uspsCheckOk(run: string): boolean {
  const d = run.replace(/\D/g, '');
  if (d.length !== 22 && d.length !== 26) return false;
  let total = 0;
  for (let i = 0; i < d.length - 1; i++) total += Number(d[d.length - 2 - i]) * (i % 2 ? 1 : 3);
  return (10 - total % 10) % 10 === Number(d[d.length - 1]);
}

/** Twin of Python _usps_tracking: a uspsCheckOk run with no Luhn-valid 16-digit window starting at its 2nd or
 * 3rd group (a card behind a short 91-95 number). */
function uspsTracking(run: string): boolean {
  const d = run.replace(/\D/g, '');
  if (!uspsCheckOk(run)) return false;
  for (let i = 4; i + 16 <= d.length; i += 4) if (luhn(d.slice(i, i + 16))) return false;
  return true;
}

const isDigit = (c: string | undefined) => c !== undefined && c >= '0' && c <= '9';

/** Twin of Python _groups_near: digit groups, joined by single spaces or dashes, running up to start and on
 * from end: [all, 4+ digits] before, then after. Each side stops after 5 groups. */
function groupsNear(text: string, start: number, end: number): number[] {
  const counts: number[] = [];
  for (const step of [-1, 1]) {
    let i = step < 0 ? start - 1 : end, total = 0, long = 0;
    while (total < 5 && i + step >= 0 && i + step < text.length && '- '.includes(text[i]) && isDigit(text[i + step])) {
      let j = i + step;
      while (isDigit(text[j + step])) j += step;
      total++;
      if (Math.abs(j - i) >= 4) long++;
      i = j + step;
    }
    counts.push(total, long);
  }
  return counts;
}

/** Twin of Python _near_ok: 1 to 4 digit groups before a later card window and at most 4 after, at most 2 of
 * 4+ digits on each side. */
function nearOk([before, beforeLong, after, afterLong]: number[]): boolean {
  return before >= 1 && before <= 4 && after <= 4 && beforeLong <= 2 && afterLong <= 2;
}

/** Twin of Python _overlapping: every match of a 'g' regex, each search starting one past the last match.
 * Runs on a copy: an early return must not leave lastIndex set on the shared regex (matchAll copies it). */
function* overlapping(source: RegExp, text: string): Generator<RegExpExecArray> {
  const re = new RegExp(source);
  for (let m; (m = re.exec(text)); re.lastIndex = m.index + 1) yield m;
}

/** Twin of Python card_hit: a standalone 16-digit run or 15-digit Amex number (dates/URLs and whole USPS
 * tracking numbers scrubbed) that passes Luhn, or a 16-digit window after other digit groups (see nearOk) that
 * passes Luhn and starts with a card-network prefix, read with every check-digit-valid USPS run removed. Without Luhn
 * (non-ASCII digits folded to 0) any card-shaped run is held and no run is exempted as a tracking number. */
function cardHit(text: string, checkLuhn: boolean): boolean {
  text = text.replace(URL_RE, ' ').replace(ISO_DATE, ' ');
  if (!checkLuhn) return text.search(CARD) >= 0 || text.search(AMEX) >= 0;
  const plain = text.replace(TRACKING, (m) => uspsCheckOk(m) ? ' ' : m);
  text = text.replace(TRACKING, (m) => uspsTracking(m) ? ' ' : m);
  for (const m of [...text.matchAll(CARD), ...text.matchAll(AMEX)]) if (realCard(m[0].replace(/\D/g, ''))) return true;
  for (const m of overlapping(CARD, plain)) {
    const d = m[0].replace(/\D/g, '');
    if (CARD_IIN.test(d) && realCard(d) && nearOk(groupsNear(plain, m.index, m.index + m[0].length))) return true;
  }
  return false;
}

export function hasSecret(text: string): boolean {
  // Normalizing folds non-ASCII digits to 0, losing their value; such a run is held without Luhn.
  const checkLuhn = !/(?![0-9])\p{Nd}/u.test(text);
  text = normalizeForScan(text);
  if (cardHit(text, checkLuhn)) return true;
  if (WORD.test(text) || TOKEN.test(text)) return true;
  for (const m of text.matchAll(GENERIC)) {
    const v = m[4];
    if (v && entropy(v) >= 3.5 && /\d/.test(v) && /[A-Za-z]/.test(v)) return true;
  }
  return false;
}

// Twin of Python MACHINE_KEYS: tool-built fields (hashes, ids, pointer names), never user text.
const MACHINE_KEYS = new Set(['sha256', 'id', 'sourceId', 'rootId', 'children', 'pointer', 'principals']);

/** hasSecret over every user-text string (keys and values) inside a request payload; MACHINE_KEYS values skipped. */
export function payloadHasSecret(obj: unknown): boolean {
  if (typeof obj === 'string') return hasSecret(obj);
  if (Array.isArray(obj)) return obj.some(payloadHasSecret);
  if (obj && typeof obj === 'object') return Object.entries(obj).some(([k, v]) => hasSecret(k) || (!MACHINE_KEYS.has(k) && payloadHasSecret(v)));
  return false;
}
