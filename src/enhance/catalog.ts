/**
 * CATALOG v2: the fetch layer's input schema, and nothing else.
 *
 * A v1 catalog record is just `{id, text}` — fetch's original shape, still
 * accepted unchanged. A v2 record adds three optional fields the local
 * narrowing pass in `fetch.ts` and the Jev judge both read:
 *
 *   - `utterances`: realistic user phrasings that should route to this
 *     record ("restart it," not "invoke the process supervisor restart
 *     skill" — see the routing research this schema is drawn from). Weighted
 *     higher than `text` in local narrowing, because a real request looks
 *     like an utterance, not like a catalog description.
 *   - `negatives`: phrasings that sound close but should NOT route here.
 *     Subtracted from the record's local score, to sharpen the boundary
 *     between similar records.
 *   - `tags`: freeform, for filtering/debug only. Never scored.
 *
 * A record with none of these fields is a plain v1 record; nothing here
 * changes v1 behaviour. `loadCatalog` accepts either shape, and any file on
 * disk that mixes v1 and v2 records in one array.
 */

/** One catalog record, v1 or v2. Only `id` and `text` are required. */
export type CatalogRecord = {
  id: string;
  text: string;
  /** Realistic user phrasings that should route to this record. */
  utterances?: string[];
  /** Phrasings that sound close but should NOT route here. */
  negatives?: string[];
  /** Freeform, for filtering/debug only. Never scored. */
  tags?: string[];
};

export class CatalogError extends Error {}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(v => typeof v === 'string');
}

/**
 * Parse and validate one already-`JSON.parse`d catalog shape: an array of
 * records, or `{catalog: [...]}`. Every record must have a non-empty `text`;
 * `id` is optional on input (the caller/sweep engine assigns one) but must be
 * a non-empty string when present. `utterances`, `negatives` and `tags`, when
 * present, must be arrays of strings. Throws `CatalogError` with a plain-words
 * reason naming the offending record index.
 */
export function loadCatalog(parsed: unknown): CatalogRecord[] {
  const list = Array.isArray(parsed) ? parsed : (parsed as { catalog?: unknown })?.catalog;
  if (!Array.isArray(list)) throw new CatalogError('The catalog must be an array of records, or an object with a "catalog" array');
  const out: CatalogRecord[] = [];
  for (const [i, entry] of list.entries()) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) throw new CatalogError(`Catalog entry ${i} is not a JSON object`);
    const record = entry as Record<string, unknown>;
    if (typeof record.text !== 'string' || !record.text.trim()) throw new CatalogError(`Catalog entry ${i} has no text`);
    if (record.id !== undefined && (typeof record.id !== 'string' || !record.id.trim())) throw new CatalogError(`Catalog entry ${i} has a non-string id`);
    if (record.utterances !== undefined && !isStringArray(record.utterances)) throw new CatalogError(`Catalog entry ${i} has a non-string-array "utterances"`);
    if (record.negatives !== undefined && !isStringArray(record.negatives)) throw new CatalogError(`Catalog entry ${i} has a non-string-array "negatives"`);
    if (record.tags !== undefined && !isStringArray(record.tags)) throw new CatalogError(`Catalog entry ${i} has a non-string-array "tags"`);
    const out_entry: CatalogRecord = { id: (record.id as string) ?? `record_${i}`, text: record.text };
    if (record.utterances !== undefined) out_entry.utterances = record.utterances as string[];
    if (record.negatives !== undefined) out_entry.negatives = record.negatives as string[];
    if (record.tags !== undefined) out_entry.tags = record.tags as string[];
    out.push(out_entry);
  }
  return out;
}

/** Parse a catalog from raw JSON text. Throws `CatalogError` for bad JSON or a bad shape. */
export function parseCatalogText(text: string): CatalogRecord[] {
  let parsed: unknown;
  try { parsed = JSON.parse(text); }
  catch { throw new CatalogError('The catalog file is not valid JSON'); }
  return loadCatalog(parsed);
}

export type CatalogValidation = {
  totalRecords: number;
  /** Records with fewer than three utterances (zero counts as thin too). */
  thinUtterances: { id: string; count: number }[];
  /** The same phrasing (case/space-normalized) appears in more than one record's utterances. */
  duplicateUtterances: { utterance: string; ids: string[] }[];
  /** A record's negative is also listed as another record's utterance — a direct contradiction. */
  negativeCollisions: { id: string; negative: string; collidesWithId: string }[];
  /** True only when none of the above lists has an entry. */
  clean: boolean;
};

/** Case/space-normalized key for comparing phrasings across records. */
function norm(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, ' ');
}

/**
 * Validate a v1-or-v2 catalog for the three problems `learn` and hand-written
 * catalogs both tend to introduce: records too thin on utterances to be
 * found by a paraphrase-heavy request, the same phrasing claimed by two
 * records (an unresolvable tie for the router), and a negative on one record
 * that is word-for-word another record's utterance (a direct contradiction
 * between what should and should not route there).
 */
export function validateCatalog(records: CatalogRecord[]): CatalogValidation {
  const thinUtterances: CatalogValidation['thinUtterances'] = [];
  const utteranceOwners = new Map<string, string[]>(); // normalized utterance -> record ids
  const negativesByRecord = new Map<string, string[]>(); // id -> raw negatives

  for (const r of records) {
    const count = r.utterances?.length ?? 0;
    if (count < 3) thinUtterances.push({ id: r.id, count });
    for (const u of r.utterances ?? []) {
      const key = norm(u);
      if (!key) continue;
      const owners = utteranceOwners.get(key) ?? [];
      if (!owners.includes(r.id)) owners.push(r.id);
      utteranceOwners.set(key, owners);
    }
    if (r.negatives?.length) negativesByRecord.set(r.id, r.negatives);
  }

  const duplicateUtterances: CatalogValidation['duplicateUtterances'] = [];
  for (const [utterance, ids] of utteranceOwners) {
    if (ids.length > 1) duplicateUtterances.push({ utterance, ids: ids.slice().sort() });
  }
  duplicateUtterances.sort((a, b) => a.utterance.localeCompare(b.utterance));

  const negativeCollisions: CatalogValidation['negativeCollisions'] = [];
  for (const [id, negatives] of negativesByRecord) {
    for (const negative of negatives) {
      const key = norm(negative);
      if (!key) continue;
      const owners = utteranceOwners.get(key);
      if (!owners) continue;
      for (const ownerId of owners) {
        if (ownerId === id) continue; // a record listing its own utterance as a negative is a separate, self-contradiction problem; not this check
        negativeCollisions.push({ id, negative, collidesWithId: ownerId });
      }
    }
  }
  negativeCollisions.sort((a, b) => a.id.localeCompare(b.id) || a.negative.localeCompare(b.negative));

  thinUtterances.sort((a, b) => a.id.localeCompare(b.id));

  return {
    totalRecords: records.length,
    thinUtterances,
    duplicateUtterances,
    negativeCollisions,
    clean: thinUtterances.length === 0 && duplicateUtterances.length === 0 && negativeCollisions.length === 0
  };
}

/** Plain-words report, one line per finding, for the CLI and for tests that just want to eyeball it. */
export function formatCatalogValidation(v: CatalogValidation): string {
  const lines = [`catalog validation: ${v.totalRecords} record(s)`];
  if (v.clean) { lines.push('  clean: no thin-utterance, duplicate-utterance or negative-collision findings'); return lines.join('\n'); }
  if (v.thinUtterances.length) {
    lines.push(`  thin utterances (< 3): ${v.thinUtterances.length} record(s)`);
    for (const t of v.thinUtterances) lines.push(`    ${t.id}: ${t.count}`);
  }
  if (v.duplicateUtterances.length) {
    lines.push(`  duplicate utterances across records: ${v.duplicateUtterances.length}`);
    for (const d of v.duplicateUtterances) lines.push(`    ${JSON.stringify(d.utterance)} claimed by: ${d.ids.join(', ')}`);
  }
  if (v.negativeCollisions.length) {
    lines.push(`  negatives that are also another record's utterance: ${v.negativeCollisions.length}`);
    for (const c of v.negativeCollisions) lines.push(`    ${c.id}'s negative ${JSON.stringify(c.negative)} is ${c.collidesWithId}'s utterance`);
  }
  return lines.join('\n');
}
