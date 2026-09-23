/** Node twin of Python has_secret / payload_has_secret (skills/super-jev/prepare_bulk.py).
 * Both load the one pattern source, skills/super-jev/secret_patterns.json. */
import { readFileSync } from 'node:fs';

const PAT = JSON.parse(readFileSync(new URL('../skills/super-jev/secret_patterns.json', import.meta.url), 'utf8'));
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

export function hasSecret(text: string): boolean {
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
