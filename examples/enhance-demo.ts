/**
 * Offline demo of the context-enhancer primitives. Runs entirely against the
 * stub provider: no API key, no network, no cost. It prints the batch plan
 * before any call, then the coverage manifest and the cost summary.
 *
 * It cannot show accuracy, latency or cost. A stub returns whatever it is
 * scripted to return. Only a live run can measure any of those.
 */
import { readFile } from 'node:fs/promises';
import {
  formatCost, formatManifest, formatPlan,
  planEnhancedRun, runEnhancedClassification,
  planInvestigation, runInvestigation,
  StubEvaluator, choiceAnswer, scriptFromTable,
  gatherEvidence,
  type EvidenceSpec, type SourceDoc, type StubScript
} from '../src/enhance/index.ts';
import type { Answer } from '../src/types.ts';

const recorded = JSON.parse(await readFile(new URL('../test/enhance/fixtures/recorded-answers.json', import.meta.url), 'utf8')) as {
  classification: Record<string, Record<string, { choice: string; confidence: number }>>;
  investigation: Record<string, Record<string, { choice: string; confidence: number }>>;
};
const { categories, documents, investigations } = await import('../test/enhance/fixtures/cases.ts');

const table = (recordedTable: Record<string, { choice: string; confidence: number }>, options: string[]): Record<string, Answer> =>
  Object.fromEntries(Object.entries(recordedTable).map(([id, a]) => [id, choiceAnswer(a.choice, a.confidence, options)]));

const rule = (title: string) => console.log(`\n${'='.repeat(72)}\n${title}\n${'='.repeat(72)}`);

// ---------------------------------------------------------------- part one
rule('1. Classification: 32 labeled records, named references, two passes');

const categoryIds = Object.keys(categories);
const records = documents.map((d: { id: string; text: string }) => ({ id: d.id, text: d.text }));
const named = table(recorded.classification.namedShuffled, categoryIds);
const array = table(recorded.classification.arrayShuffled, categoryIds);

/** Answers come from whichever recorded table matches the framing of the request. */
const byFraming: StubScript = (request, index) => {
  const keyed = !Array.isArray((request.state as { records: unknown }).records);
  return scriptFromTable(keyed ? named : array, { usage: { input_tokens: 900, output_tokens: 120 } })(request, index);
};

const classifyConfig = {
  records,
  options: categories as Record<string, string>,
  abstainOption: 'other',
  passes: [
    { name: 'array-forward', framing: 'array' as const, order: 'input' as const },
    { name: 'keyed-forward', framing: 'keyed' as const, order: 'input' as const }
  ]
};

console.log('\n-- the plan, produced before any call --');
for (const { pass, plan } of planEnhancedRun(classifyConfig).plans) {
  console.log(`\npass "${pass}"`);
  console.log(formatPlan(plan));
}

const classification = await runEnhancedClassification(classifyConfig, new StubEvaluator({ script: byFraming, validate: true }));
console.log('\n-- the coverage manifest --');
console.log(formatManifest(classification.manifest));
console.log('\n-- the cost --');
console.log(formatCost(classification.cost));

console.log('\n-- the two records the pilot accepted wrongly --');
for (const id of ['D09', 'D22']) {
  const outcome = classification.manifest.outcomes.find(o => o.id === id)!;
  const label = documents.find((d: { id: string; expected: string }) => d.id === id)!.expected;
  console.log(`${id}: outcome=${outcome.kind} label=${label}`);
  console.log(`  passes: ${outcome.passes.map(p => `p${p.pass}=${p.value}@${p.confidence}`).join('  ')}`);
  console.log(`  reason: ${outcome.reason}`);
}

// ---------------------------------------------------------------- part two
rule('2. Linked evidence: ticket -> order -> policy, checked in code first');

const pool: SourceDoc[] = investigations.flatMap((c: { docs: SourceDoc[] }) => c.docs);
const eligibility = {
  eligible: 'Return allowed by the applicable policy',
  ineligible: 'Return disallowed by the applicable policy',
  unknown: 'Insufficient, ambiguous or conflicting evidence'
};
const spec: EvidenceSpec = {
  name: 'ticket -> order -> policy',
  maxDepth: 3,
  maxDocs: 16,
  proseReferences: true,
  roles: [
    { name: 'ticket', identify: (doc, rootId) => doc.id === rootId },
    { name: 'order', identify: doc => /purchase age:\s*\d+/i.test(doc.text) },
    { name: 'policy', identify: doc => /\beligible\b/i.test(doc.text) }
  ]
};
const investigateConfig = {
  cases: investigations.map((c: { id: string; query: string }) => ({ id: c.id, question: c.query })),
  pool, spec, options: eligibility, abstainOption: 'unknown'
};

const planned = planInvestigation(investigateConfig);
console.log('\n-- evidence checked in code, before any call --');
for (const c of investigateConfig.cases) {
  const report = planned.evidence[c.id];
  const verdict = report.complete ? 'ASK' : 'BLOCK';
  console.log(`${c.id}: ${verdict}  sources=[${report.sourceIds.join(', ')}]`);
  for (const problem of report.problems) console.log(`    blocked: ${problem}`);
  for (const warning of report.warnings) console.log(`    note: ${warning}`);
}
console.log(`\n${formatPlan(planned.plan)}`);

const investigation = await runInvestigation(investigateConfig, new StubEvaluator({
  script: scriptFromTable(table(recorded.investigation.structuredPlusProse, Object.keys(eligibility)), { usage: { input_tokens: 700, output_tokens: 90 } }),
  validate: true
}));
console.log('\n-- the coverage manifest --');
console.log(formatManifest(investigation.manifest));
console.log('\n-- the cost --');
console.log(formatCost(investigation.cost));

// -------------------------------------------------------------- part three
rule('3. Traversal bounds: depth, cycles and superseded documents');

for (const [label, caseId, override] of [
  ['bounded depth', 'T1', { maxDepth: 1 }],
  ['cycle detected', 'T12', {}],
  ['superseded policy dropped', 'T11', {}],
  ['missing policy', 'T9', {}]
] as const) {
  const report = gatherEvidence(caseId, pool, { ...spec, ...override });
  console.log(`\n${label} (${caseId}): complete=${report.complete}`);
  console.log(`  visited: ${report.traversal.visitedIds.join(' -> ')}`);
  if (report.traversal.cycleEdges.length) console.log(`  cycles: ${report.traversal.cycleEdges.map(e => `${e.from}->${e.to}`).join(', ')}`);
  for (const problem of report.problems) console.log(`  blocked: ${problem}`);
  for (const warning of report.warnings) console.log(`  note: ${warning}`);
}

rule('What this run did NOT show');
console.log([
  '- No accuracy result. The stub replayed scripted answers; it cannot be right or wrong.',
  '- No latency or cost result. The stub does not call a provider.',
  '- No evidence that a large document survives chunking with its facts intact.',
  '- No retrieval. The traversal follows links that already exist in the data.',
  '- No real-data validation. Every record here is synthetic.',
  'See docs/enhancer.md for the full list.'
].join('\n'));

if (!classification.manifest.complete || !investigation.manifest.complete) {
  console.error('\nA coverage manifest was incomplete. That is a failure.');
  process.exitCode = 1;
}
