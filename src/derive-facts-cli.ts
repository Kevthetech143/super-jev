#!/usr/bin/env node
/**
 * `node src/derive-facts-cli.ts` — the process boundary for the derived-facts
 * pattern (see src/enhance/derive-facts.ts), for callers that are not
 * TypeScript. Reads one JSON object from stdin: `{"evidence": Evidence,
 * "claims": string[]}`. Prints one JSON object to stdout:
 * `{"facts": DerivedFact[], "formatted": string, "verdicts": PreRuleVerdict[]}`.
 *
 * Pure and offline: this never calls TypeSafe, git, gh, or anything else.
 * Whatever evidence the caller already gathered goes in as JSON; the facts
 * and pre-rule verdicts come out as JSON. This is the bridge
 * `skills/super-jev/superjev.py`'s Python fallback path uses when the
 * fleet-local worker-verify door is not installed — see cmd_verify's
 * docstring there for why a fallback exists at all.
 */
import { deriveFacts, formatDerivedFacts, preRules, type Evidence } from './enhance/derive-facts.ts';

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const raw = await readStdin();
  let input: { evidence?: Evidence; claims?: string[] };
  try {
    input = JSON.parse(raw || '{}');
  } catch {
    console.error('derive-facts-cli: stdin must be JSON: {"evidence": {...}, "claims": [...]}');
    process.exitCode = 1;
    return;
  }
  const evidence = input.evidence ?? {};
  const claims = input.claims ?? [];
  const facts = deriveFacts(evidence);
  const verdicts = preRules(claims, facts);
  process.stdout.write(JSON.stringify({ facts, formatted: formatDerivedFacts(facts), verdicts }, null, 2) + '\n');
}

main();
