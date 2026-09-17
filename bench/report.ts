/**
 * Report rendering. Tables and prose only, no scoring: every number here comes
 * from metrics.ts, so the report cannot disagree with the results file.
 */
import type { Metrics } from './metrics.ts';
import { GATE_MIN_CONFIDENCE, PILOT_REFERENCE } from './conditions.ts';

export type ReportInput = {
  mode: 'live' | 'stub';
  startedAt: string;
  finishedAt: string;
  outDir: string;
  sets: { id: string; note: string }[];
  /** Per set id, one metrics row per condition, in condition order. */
  bySet: Record<string, Metrics[]>;
  /** Combined 48-record rows, one per condition. */
  combined: Metrics[];
  plannedCalls: number;
  plannedInputTokens: number;
  /** The instruction text actually sent, read from the builders themselves. */
  prompts: { baseline: string; enhancer: string };
};

const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
const frac = (n: number, total: number) => `${n}/${total} (${pct(total ? n / total : 0)})`;

function otherKindSummary(m: Metrics): string {
  const entries = Object.entries(m.otherKinds).filter(([, n]) => n > 0);
  return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(', ') : 'none';
}

function table(rows: Metrics[]): string {
  const header = [
    'Condition', 'Order', 'Accuracy', 'Pass-1 accuracy', 'Automation coverage',
    'ACCEPTED-ERROR RATE', 'Review', 'Abstain', 'Other outcomes', 'Manifest complete',
    'Calls', 'Input tokens', 'Output tokens', 'Median ms', 'p95 ms', 'Wall s', 'Rejected calls'
  ];
  const lines = [`| ${header.join(' | ')} |`, `| ${header.map(() => '---').join(' | ')} |`];
  for (const m of rows) {
    lines.push(`| ${[
      m.condition,
      m.order,
      frac(m.correct, m.total),
      frac(m.pass1Correct, m.total),
      frac(m.accepted, m.total),
      frac(m.acceptedWrong, m.total),
      frac(m.review, m.total),
      frac(m.abstain, m.total),
      otherKindSummary(m),
      m.manifestComplete ? 'yes' : 'NO',
      String(m.calls),
      `${m.inputTokens}${m.tokensIncomplete ? ' (incomplete)' : ''}`,
      `${m.outputTokens}${m.tokensIncomplete ? ' (incomplete)' : ''}`,
      String(m.medianLatencyMs),
      String(m.p95LatencyMs),
      (m.wallMs / 1000).toFixed(1),
      String(m.validationRejections)
    ].join(' | ')} |`);
  }
  return lines.join('\n');
}

function acceptedWrongSection(rows: Metrics[]): string {
  const lines: string[] = [];
  for (const m of rows) {
    if (!m.acceptedWrongList.length) {
      lines.push(`- ${m.condition} (${m.set}): no accepted errors.`);
      continue;
    }
    lines.push(`- ${m.condition} (${m.set}): ${m.acceptedWrongList.length} accepted error(s).`);
    for (const w of m.acceptedWrongList) {
      lines.push(`  - ${w.id}${w.kind ? ` [${w.kind}]` : ''}: chose \`${w.chose}\`, label \`${w.expected}\`, confidence ${w.confidence ?? 'n/a'}`);
    }
  }
  return lines.join('\n');
}

export function renderReport(input: ReportInput): string {
  const allRows = [...Object.values(input.bySet).flat(), ...input.combined];
  const models = [...new Set(allRows.flatMap(m => m.models))];
  const conditions = input.combined;
  const totalCalls = conditions.reduce((n, m) => n + m.calls, 0);
  const totalInput = conditions.reduce((n, m) => n + m.inputTokens, 0);
  const totalOutput = conditions.reduce((n, m) => n + m.outputTokens, 0);
  const problems = allRows.flatMap(m => m.manifestProblems);
  const rejections = allRows.flatMap(m => m.rejectionMessages);

  const parts: string[] = [];
  parts.push('# Live measurement: baseline organizer vs context enhancer');
  parts.push('');
  parts.push(`- Mode: **${input.mode}**${input.mode === 'stub' ? ' (offline stub provider, no network, no accuracy evidence)' : ' (live provider)'}`);
  parts.push(`- Started: ${input.startedAt}`);
  parts.push(`- Finished: ${input.finishedAt}`);
  parts.push(`- Model field returned by the provider: ${models.length ? models.map(m => `\`${m}\``).join(', ') : 'none recorded'}`);
  parts.push(`- Gate: accept only at confidence >= ${GATE_MIN_CONFIDENCE}, agreement required across passes, abstain option \`other\``);
  parts.push(`- Calls: ${totalCalls} actual against ${input.plannedCalls} planned; input tokens ${totalInput}, output tokens ${totalOutput}`);
  parts.push(`- Raw requests and responses: \`${input.outDir}/raw.jsonl\`, machine-readable results: \`${input.outDir}/results.json\``);
  parts.push('');
  parts.push('## How to read the columns');
  parts.push('');
  parts.push('- **Accuracy** is the harness\'s final value against the label, over every record. A record the harness blocked for disagreement has no final value and counts as wrong here, which is deliberate: a blocked record is not a correct answer, it is a deferred one.');
  parts.push('- **Pass-1 accuracy** is the first pass\'s raw answer against the label. It is the apples-to-apples model accuracy across one-pass and two-pass conditions.');
  parts.push('- **Automation coverage** is the share of records accepted for automatic action with no human.');
  parts.push('- **Accepted-error rate** is the share of records that were both wrong and accepted. This is the number that matters. Everything else is context for it.');
  parts.push('- **Review**, **Abstain** and **Other outcomes** are the records a human still has to touch. Coverage plus these always accounts for every record.');
  parts.push('- **Rejected calls** are whole responses the validator refused. Every record in such a call is lost, which is why the count is reported per condition and not buried.');
  parts.push('');

  parts.push('## The prompts actually sent');
  parts.push('');
  parts.push('Both strings below are read out of the shipped builders at report time, so this section cannot drift from what went on the wire.');
  parts.push('');
  parts.push('Conditions A and B, positional array references, built by the organizer itself:');
  parts.push('');
  parts.push('```');
  parts.push(input.prompts.baseline);
  parts.push('```');
  parts.push('');
  parts.push('Conditions C through F, named record references, built by the enhancer:');
  parts.push('');
  parts.push('```');
  parts.push(input.prompts.enhancer);
  parts.push('```');
  parts.push('');
  parts.push('Two differences from the original pilot are worth naming, because they are the reason this is a fresh measurement and not a replay. The baseline string is byte-identical to the pilot\'s array prompt. The enhancer\'s named-reference string differs from the pilot\'s named-path probe by one clause, "no option fits" where the probe said "no category fits", and the enhancer\'s keyed state carries each record\'s own id alongside its text where the probe carried text alone. Neither difference was introduced for this run; both are what the enhancer ships.');
  parts.push('');

  for (const set of input.sets) {
    const rows = input.bySet[set.id] ?? [];
    parts.push(`## Record set \`${set.id}\``);
    parts.push('');
    parts.push(set.note);
    parts.push('');
    parts.push(table(rows));
    parts.push('');
    const reference = Object.entries(PILOT_REFERENCE)
      .filter(([, byId]) => byId[set.id])
      .map(([condition, byId]) => `${condition}: ${byId[set.id]}`);
    if (reference.length) {
      parts.push(`Pilot figures for the same conditions on this set, carried as context only and never used in any scoring above: ${reference.join('; ')}.`);
      parts.push('');
    }
    parts.push('Accepted errors:');
    parts.push('');
    parts.push(acceptedWrongSection(rows));
    parts.push('');
  }

  parts.push('## Combined, all 48 records');
  parts.push('');
  parts.push('Counts add across the two sets; every rate is recomputed from the combined counts rather than averaged across sets.');
  parts.push('');
  parts.push(table(input.combined));
  parts.push('');
  parts.push('Accepted errors, combined:');
  parts.push('');
  parts.push(acceptedWrongSection(input.combined));
  parts.push('');

  parts.push('## Conditions');
  parts.push('');
  for (const m of input.combined) parts.push(`- **${m.condition}**: ${m.conditionLabel}`);
  parts.push('');

  parts.push('## Failures and bookkeeping');
  parts.push('');
  parts.push(problems.length ? `Coverage manifest problems: ${[...new Set(problems)].join('; ')}` : 'Every condition accounted for every input record exactly once.');
  parts.push('');
  parts.push(rejections.length ? `Validation rejections and call failures: ${[...new Set(rejections)].join('; ')}` : 'No response was rejected by the validator and no call failed.');
  parts.push('');

  parts.push('## What this does and does not show');
  parts.push('');
  parts.push([
    'This is 48 synthetic records, hand-written to be adversarial, run once per condition with one order seed each.',
    'It is a measurement, not an experiment: there is no repetition at a fixed order, no confidence interval and no significance claim, and a difference of one or two records between two conditions is inside the noise this design cannot resolve.',
    'The labels were fixed before any call in the original pilot and are copied verbatim here, so the scoring is not tuned to the answers, but the records are still our own writing and not a sample of real traffic.',
    'No real customer data was used and none should be: these figures say nothing about accuracy on production documents, on other languages, on other domains or on a different provider model.',
    'Latency and token figures come from this machine and this session, so they carry the network and the provider load of the moment.',
    'Per the provider terms of service, these results are internal only and are not a public benchmark or a comparative claim about the provider.'
  ].join(' '));
  parts.push('');
  return parts.join('\n');
}
