import type { Answer, EnhanceRecord, Evaluation, MappingReport, Question, RecordRef, Request } from './types.ts';

const SAFE_KEY = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const FORBIDDEN_KEYS = new Set(['constructor', 'prototype', '__proto__', 'toString', 'hasOwnProperty']);

/**
 * Assign stable ids to records that do not carry one. The default shape is
 * `records/<n>`, matching the reference path the request describes.
 */
export function assignIds(records: { id?: string; text: string }[]): EnhanceRecord[] {
  const seen = new Set<string>();
  return records.map((r, i) => {
    const id = r.id?.trim() || `records/${i}`;
    if (seen.has(id)) throw new Error(`Duplicate record id: ${id}`);
    seen.add(id);
    if (typeof r.text !== 'string' || !r.text.trim()) throw new Error(`Record ${id} has no text`);
    return { id, text: r.text };
  });
}

/**
 * Derive a safe question key from an arbitrary record id, and keep the pairing
 * explicit. Answers come back keyed by this key, so the key is the only thing
 * that maps an answer to a record. Array position is never used.
 */
export function toRefs(records: EnhanceRecord[]): RecordRef[] {
  const used = new Set<string>();
  return records.map((r, i) => {
    let key = r.id.replace(/[^A-Za-z0-9_]/g, '_');
    if (!SAFE_KEY.test(key) || FORBIDDEN_KEYS.has(key)) key = `r_${key}`.slice(0, 64);
    if (!SAFE_KEY.test(key)) key = `r_${i}`;
    let candidate = key, n = 2;
    while (used.has(candidate)) candidate = `${key}_${n++}`;
    used.add(candidate);
    return { id: r.id, key: candidate, text: r.text };
  });
}

/**
 * Build a request whose state is an object keyed by record key and whose
 * questions are keyed the same way, each naming its own `records.<key>.text`
 * path. This is the named-record-reference form; the alternative is a
 * positional array with `records[i]` paths.
 */
export function buildKeyedRequest(refs: RecordRef[], question: (ref: RecordRef) => Question): Request {
  return {
    state: { records: Object.fromEntries(refs.map(r => [r.key, { id: r.id, text: r.text }])) },
    questions: Object.fromEntries(refs.map(r => [r.key, question(r)]))
  };
}

/** Positional-array form, kept so the two framings can be compared offline. */
export function buildArrayRequest(refs: RecordRef[], question: (ref: RecordRef, index: number) => Question): Request {
  return {
    state: { records: refs.map(r => ({ id: r.id, text: r.text })) },
    questions: Object.fromEntries(refs.map((r, i) => [`record_${i}`, question(r, i)]))
  };
}

/** The key actually sent to the provider, paired with the record it stands for. */
export type AskedPair = { key: string; id: string };

/** Pairing for the keyed framing: the question key is the record's own key. */
export function keyedPairs(refs: RecordRef[]): AskedPair[] {
  return refs.map(r => ({ key: r.key, id: r.id }));
}

/** Pairing for the array framing: the question key is the position. */
export function arrayPairs(refs: RecordRef[]): AskedPair[] {
  return refs.map((r, i) => ({ key: `record_${i}`, id: r.id }));
}

/**
 * Map a response back to records, one record at a time.
 *
 * The caller passes the key-to-record pairing it actually sent, so the two
 * framings share this code and neither can drift into matching by position.
 * - every asked key must produce exactly one answer;
 * - an asked key with no answer is reported as missing, and the caller turns
 *   that into an `unanswered` outcome;
 * - an answer key nobody asked for is rejected outright, never attached to a
 *   record by position or by guesswork.
 */
export function mapAnswers(pairs: AskedPair[], evaluation: Evaluation): MappingReport {
  const seen = new Set<string>();
  for (const pair of pairs) {
    if (seen.has(pair.key)) throw new Error(`Asked key ${pair.key} was sent twice`);
    seen.add(pair.key);
  }
  const answers: Record<string, Answer> = evaluation?.answers ?? {};
  const answered: MappingReport['answered'] = [];
  const missing: MappingReport['missing'] = [];
  for (const pair of pairs) {
    const answer = Object.hasOwn(answers, pair.key) ? answers[pair.key] : undefined;
    if (answer === undefined || answer === null) missing.push(pair);
    else answered.push({ ...pair, answer });
  }
  const rejected = Object.keys(answers).filter(k => !seen.has(k));
  return { asked: pairs, answered, missing, rejected };
}

/** True when every asked key produced exactly one answer and nothing extra came back. */
export function mappingIsExact(report: MappingReport): boolean {
  return report.missing.length === 0 && report.rejected.length === 0 && report.answered.length === report.asked.length;
}
