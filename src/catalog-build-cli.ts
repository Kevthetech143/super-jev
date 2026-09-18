#!/usr/bin/env node
/**
 * `npm run catalog:build -- <skills-dir> <out.json>`
 *
 * A zero-dependency generator that walks a Claude Code skills directory
 * (one subdirectory per skill, each holding a SKILL.md) and emits a
 * catalog v2 JSON file: one record per skill, built entirely from the
 * skill's own frontmatter `description` and any "Trigger"/"Triggers"/
 * "Use when" lines in its body. No hand tuning, no network call — this is
 * the generic, repo-wide sibling of the hand-curated catalog kept in
 * super-jev-experiments.
 *
 * Record shape:
 *   { id, text, utterances: string[], negatives: [], tags: [folderName] }
 */
import { readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

class CliError extends Error {}

export const usage = `super-jev catalog:build <skills-dir> <out.json>

Walks <skills-dir> for <name>/SKILL.md files, reads each skill's
frontmatter "description" and any body lines starting with
"Trigger:"/"Triggers:"/"Use when", and writes a catalog v2 JSON array to
<out.json>:

  { "id": "<folder name>",
    "text": "<description, capped at 300 chars>",
    "utterances": ["<trigger phrases>", "/<folder name>", "<auto variant 1>", "<auto variant 2>"],
    "negatives": [],
    "tags": ["<folder name>"] }

Auto variant 1: the description, lowercased, punctuation stripped.
Auto variant 2: the description's first clause (up to the first ., ; or ,).

Skips subdirectories with no SKILL.md. Exit codes: 0 ok, 1 usage/parse error.`;

const TEXT_CAP = 300;

export type CatalogV2Record = {
  id: string;
  text: string;
  utterances: string[];
  negatives: string[];
  tags: string[];
};

/** Strip a single layer of matching quotes (") from a trimmed string. */
function unquote(raw: string): string {
  const s = raw.trim();
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1).replace(/\\"/g, '"');
  return s;
}

/**
 * Pull the YAML frontmatter block (between the first two `---` lines) out
 * of a SKILL.md, then pull `description:` out of that block. Handles both
 * a single-line quoted/bare value and a block scalar (`description: |` or
 * `description: >`, followed by indented lines).
 */
export function extractDescription(source: string): string {
  const lines = source.split('\n');
  if (lines[0]?.trim() !== '---') return '';
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === '---') { end = i; break; }
  }
  if (end === -1) return '';
  const front = lines.slice(1, end);

  for (let i = 0; i < front.length; i++) {
    const m = /^description:\s?(.*)$/.exec(front[i]);
    if (!m) continue;
    const rest = m[1];
    if (rest === '|' || rest === '>' || rest === '' || rest === '|-' || rest === '>-') {
      // Block scalar: gather subsequent indented lines until dedent.
      const bodyLines: string[] = [];
      for (let j = i + 1; j < front.length; j++) {
        if (front[j].trim() === '' ) { bodyLines.push(''); continue; }
        if (!/^\s/.test(front[j])) break;
        bodyLines.push(front[j].trim());
      }
      const joined = rest === '|' || rest === '|-'
        ? bodyLines.join(' ').replace(/\s+/g, ' ').trim()
        : bodyLines.join(' ').replace(/\s+/g, ' ').trim();
      return joined;
    }
    return unquote(rest);
  }
  return '';
}

/**
 * Find "Trigger:"/"Triggers:"/"Use when" lines in the SKILL.md body (after
 * the frontmatter) and pull out quoted phrases, backtick-wrapped slash
 * commands, and any remaining bare /slash-token, in order, deduped.
 */
export function extractTriggerPhrases(source: string): string[] {
  const lines = source.split('\n');
  let bodyStart = 0;
  if (lines[0]?.trim() === '---') {
    for (let i = 1; i < lines.length; i++) {
      if (lines[i].trim() === '---') { bodyStart = i + 1; break; }
    }
  }
  const body = lines.slice(bodyStart);

  const phrases: string[] = [];
  const seen = new Set<string>();
  const push = (raw: string) => {
    const p = raw.trim().replace(/[.:;]+$/, '').trim();
    if (!p) return;
    const key = p.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    phrases.push(p);
  };

  const triggerLine = /^\**\s*(Triggers?|Use when)\**\s*:?\s*(.*)$/i;
  for (const line of body) {
    const m = triggerLine.exec(line.trim());
    if (!m) continue;
    let rest = m[2] ?? '';
    if (/^Use when$/i.test(m[1]) && !rest) continue; // "Use when:" with body on later lines

    // Pull quoted phrases first.
    for (const qm of rest.matchAll(/"([^"]+)"/g)) push(qm[1]);
    rest = rest.replace(/"([^"]+)"/g, ' ');

    // Pull backtick-wrapped tokens next (usually /slash-commands).
    for (const bm of rest.matchAll(/`([^`]+)`/g)) push(bm[1]);
    rest = rest.replace(/`([^`]+)`/g, ' ');

    // Anything left that looks like a bare /slash-command.
    for (const sm of rest.matchAll(/\/[a-zA-Z][\w-]*/g)) push(sm[0]);
  }
  return phrases;
}

function firstClause(text: string): string {
  const m = /^(.*?)[.,;]/.exec(text);
  const clause = m ? m[1] : text;
  return clause.trim();
}

function lowercaseNoPunct(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s/-]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export function buildRecord(folderName: string, skillMd: string): CatalogV2Record {
  const description = extractDescription(skillMd);
  const text = description.length > TEXT_CAP ? description.slice(0, TEXT_CAP) : description;
  const triggerPhrases = extractTriggerPhrases(skillMd);

  const utterances: string[] = [...triggerPhrases];
  const slashName = `/${folderName}`;
  if (!utterances.some((u) => u.toLowerCase() === slashName.toLowerCase())) utterances.push(slashName);

  if (description) {
    const variant1 = lowercaseNoPunct(description);
    const variant2 = firstClause(description);
    for (const v of [variant1, variant2]) {
      if (v && !utterances.some((u) => u.toLowerCase() === v.toLowerCase())) utterances.push(v);
    }
  }

  return { id: folderName, text, utterances, negatives: [], tags: [folderName] };
}

async function isSkillDir(dirPath: string): Promise<boolean> {
  try {
    const info = await stat(join(dirPath, 'SKILL.md'));
    return info.isFile();
  } catch {
    return false;
  }
}

export async function buildCatalog(skillsDir: string): Promise<CatalogV2Record[]> {
  let entries;
  try { entries = await readdir(skillsDir, { withFileTypes: true }); }
  catch { throw new CliError(`Cannot read skills directory: ${skillsDir}`); }

  const names = entries.filter((e) => e.isDirectory()).map((e) => e.name).sort();
  const records: CatalogV2Record[] = [];
  for (const name of names) {
    const dirPath = join(skillsDir, name);
    if (!(await isSkillDir(dirPath))) continue;
    const skillMd = await readFile(join(dirPath, 'SKILL.md'), 'utf8');
    records.push(buildRecord(name, skillMd));
  }
  return records;
}

async function main(): Promise<number> {
  const args = process.argv.slice(2);
  if (!args.length || args.includes('--help')) { console.log(usage); return 0; }
  if (args.length < 2) throw new CliError(usage);

  const skillsDir = resolve(args[0]);
  const outPath = resolve(args[1]);

  let dirInfo;
  try { dirInfo = await stat(skillsDir); }
  catch { throw new CliError(`Skills directory does not exist: ${skillsDir}`); }
  if (!dirInfo.isDirectory()) throw new CliError(`Not a directory: ${skillsDir}`);

  const records = await buildCatalog(skillsDir);
  await writeFile(outPath, JSON.stringify(records, null, 2) + '\n', 'utf8');
  console.error(`Wrote ${records.length} catalog record(s) to ${outPath}`);
  return 0;
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  main()
    .then((code) => { process.exitCode = code; })
    .catch((error) => {
      if (error instanceof CliError) { console.error(error.message); process.exitCode = 1; }
      else { console.error('Unexpected error:', error); process.exitCode = 1; }
    });
}
