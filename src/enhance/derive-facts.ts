/**
 * Derived facts and pre-rules — the "read the evidence in code, not in the
 * judge" pattern from worker-verify (~/.claude/skills/worker-verify/verify.py).
 *
 * The premise, learned the hard way on 2026-09-17: a judge model reading raw
 * command output makes false-positive contradictions when several blocks
 * mention the same kind of number (a commit subject says "86 tests", the real
 * test run says "134 tests", and the judge picks the wrong one). The fix is
 * not a better prompt. It is to state the answer as a plain-English FACT,
 * computed once in code from already-gathered command output, and to hand the
 * judge the fact instead of the raw text. Every fact still carries its own
 * BACKING — the literal command and output it came from — so a fact can
 * always be audited back to the text it was read from.
 *
 * `deriveFacts` is pure: it takes structured, ALREADY-GATHERED evidence (the
 * text a git/test/gh command already printed) and returns one `DerivedFact`
 * per checkable atom, each carrying a `sentence` ready to print verbatim.
 * `preRules` is pure: it takes a list of claim strings plus the facts just
 * derived, and settles for free any claim a fact flatly contradicts, so a
 * disagreement is closed by arithmetic before any judge call. Both functions
 * do no I/O — whatever harness calls them (a Python worker-verify port, this
 * repo's own CLI, or anything else) is responsible for actually running the
 * git/test/gh commands and handing the output in.
 */

// --------------------------------------------------------------- facts

export type PushStateFact = {
  kind: 'push-state';
  branch: string;
  hasUpstream: boolean;
  ahead: number;
  behind: number;
  sentence: string;
};

export type TrackedFact = {
  kind: 'tracked';
  path: string;
  existsOnDisk: boolean;
  tracked: boolean;
  sentence: string;
};

export type LengthFact = {
  kind: 'length';
  path: string;
  exists: boolean;
  lines: number | null;
  sentence: string;
};

export type DiffstatFact = {
  kind: 'diffstat';
  base: string;
  head: string;
  filesChanged: number;
  insertions: number;
  deletions: number;
  sentence: string;
};

export type CommitFact = {
  kind: 'commit';
  hash: string;
  branch: string;
  inLog: boolean;
  sentence: string;
};

export type BranchFact = {
  kind: 'branch';
  name: string;
  exists: boolean;
  sentence: string;
};

export type TestsFact = {
  kind: 'tests';
  command: string;
  collected: number | null;
  passed: number | null;
  failed: number | null;
  sentence: string;
};

export type PrStateFact = {
  kind: 'pr-state';
  number: number;
  state: string;
  isDraft: boolean;
  headRefName: string | null;
  mergedAt: string | null;
  sentence: string;
};

export type PrChecksFact = {
  kind: 'pr-checks';
  number: number;
  total: number;
  passing: number;
  failing: string[];
  sentence: string;
};

export type LiveCallsFact = {
  kind: 'live-calls';
  sentence: string;
};

export type DerivedFact =
  | PushStateFact
  | TrackedFact
  | LengthFact
  | DiffstatFact
  | CommitFact
  | BranchFact
  | TestsFact
  | PrStateFact
  | PrChecksFact
  | LiveCallsFact;

// --------------------------------------------------------------- evidence in

/** Already-gathered git/test/gh output. Every field is optional — a harness
 * hands in only what it actually collected, and `deriveFacts` says nothing
 * about a block that was never gathered (silence, not a guess). */
export type Evidence = {
  pushState?: { branch: string; hasUpstream: boolean; ahead: number; behind: number };
  tracked?: { path: string; existsOnDisk: boolean; tracked: boolean }[];
  lengths?: { path: string; exists: boolean; lines?: number }[];
  diffstat?: { base: string; head: string; filesChanged: number; insertions: number; deletions: number };
  commits?: { hash: string; branch: string; inLog: boolean }[];
  branches?: { name: string; exists: boolean }[];
  /** `output` is the whole run's stdout+stderr; parsed the same way
   * worker-verify greps a run's own summary lines out of the whole output. */
  tests?: { command: string; output: string };
  prs?: {
    number: number;
    state?: { state: string; isDraft: boolean; headRefName?: string; mergedAt?: string | null };
    checks?: { name: string; state: string }[];
  }[];
};

// --------------------------------------------------------------- test-count parsing
// Ported from worker-verify's _SUMMARY_RX: THIS run's own count lines, pulled
// out of the whole output, so a commit subject or a changelog line elsewhere
// in the same evidence is never mistaken for a live test count.
const SUMMARY_LINE = new RegExp(
  [
    '^\\s*(?:ℹ)\\s*(?:tests|suites|pass|fail|cancelled|skipped|todo)\\s+\\d+', // node --test
    '^\\s*\\d+\\s+(?:passing|failing|pending)\\b', // mocha
    '^\\s*=+.*?\\b\\d+\\s+(?:passed|failed|error)', // pytest banner
    '\\b\\d+\\s+(?:passed|failed|xfailed|xpassed|deselected|errors?)\\b', // pytest/vitest
    '^\\s*collected\\s+\\d+\\s+items?', // pytest collection
    '^\\s*(?:Tests|Test\\s+Suites|Test\\s+Files|Snapshots|Assertions)\\s*:', // jest/vitest
    '^\\s*Ran\\s+\\d+\\s+tests?\\b', // unittest
    '^\\s*(?:OK|FAILED)\\s*(?:\\(|$)', // unittest verdict
    '^\\s*(?:ok|FAIL|PASS|---\\s*FAIL)\\s+\\S', // go test
    '^\\s*test\\s+result:\\s', // cargo
    '^\\s*\\d+\\s*/\\s*\\d+\\s+(?:passed|pass)\\b' // our own harnesses
  ].join('|'),
  'i'
);

/** THIS run's own summary lines, out of the whole output, as `lineno:text`. */
export function testSummaryLines(output: string): string[] {
  const lines = (output || '').split('\n');
  const out: string[] = [];
  lines.forEach((line, i) => {
    if (line.trim() && SUMMARY_LINE.test(line)) out.push(`${i + 1}:${line.trim()}`);
  });
  return out;
}

const NODE_TEST_COUNTS = /ℹ\s*(tests|suites|pass|fail|cancelled|skipped|todo)\s+(\d+)/gi;
const PYTEST_COUNTS = /\b(\d+)\s+(passed|failed|xfailed|xpassed|deselected|errors?)\b/gi;

/** Best-effort collected/passed/failed out of a run's own summary lines. Any
 * count this cannot parse stays `null` rather than guessed. */
export function parseTestCounts(summary: string[]): { collected: number | null; passed: number | null; failed: number | null } {
  let passed: number | null = null;
  let failed: number | null = null;
  let collected: number | null = null;
  const text = summary.join('\n');
  for (const m of text.matchAll(NODE_TEST_COUNTS)) {
    const [, word, n] = m;
    const num = parseInt(n, 10);
    if (/tests/i.test(word)) collected = num;
    if (/pass/i.test(word)) passed = num;
    if (/fail/i.test(word)) failed = num;
  }
  for (const m of text.matchAll(PYTEST_COUNTS)) {
    const [, n, word] = m;
    const num = parseInt(n, 10);
    if (/passed/i.test(word)) passed = num;
    if (/failed/i.test(word)) failed = num;
  }
  const collectedMatch = text.match(/collected\s+(\d+)\s+items?/i);
  if (collectedMatch) collected = parseInt(collectedMatch[1], 10);
  if (collected === null && passed !== null) collected = passed + (failed ?? 0);
  return { collected, passed, failed };
}

// --------------------------------------------------------------- deriveFacts

/**
 * Turn already-gathered evidence into one plain-English `DerivedFact` per
 * checkable atom the evidence covers. Order matches worker-verify's DERIVED
 * FACTS block. Nothing here calls a shell, a judge, or a network — every
 * field of `evidence` is text/JSON some other layer already collected.
 */
export function deriveFacts(evidence: Evidence): DerivedFact[] {
  const facts: DerivedFact[] = [];

  if (evidence.pushState) {
    const { branch, hasUpstream, ahead, behind } = evidence.pushState;
    let sentence: string;
    if (!hasUpstream) {
      sentence = `branch ${branch} has NO upstream; push state is unprovable.`;
    } else if (ahead === 0) {
      sentence = `branch ${branch} is 0 commits ahead of its upstream; every commit is pushed.`;
    } else {
      sentence = `branch ${branch} has ${ahead} commit${ahead === 1 ? '' : 's'} NOT pushed` +
        (behind ? `, and is ${behind} behind its upstream` : '') + '.';
    }
    facts.push({ kind: 'push-state', branch, hasUpstream, ahead, behind, sentence });
  }

  for (const t of evidence.tracked ?? []) {
    const sentence = !t.existsOnDisk
      ? `${t.path} does not exist on disk and is NOT TRACKED in git.`
      : t.tracked
        ? `${t.path} is TRACKED in git.`
        : `${t.path} is NOT TRACKED in git. It exists on disk but is in no commit.`;
    facts.push({ kind: 'tracked', path: t.path, existsOnDisk: t.existsOnDisk, tracked: t.tracked, sentence });
  }

  for (const l of evidence.lengths ?? []) {
    const sentence = !l.exists
      ? `${l.path} does not exist on disk, so anything claimed about its contents or its length is FALSE.`
      : `${l.path} is ${l.lines ?? '?'} lines long, right now.`;
    facts.push({ kind: 'length', path: l.path, exists: l.exists, lines: l.lines ?? null, sentence });
  }

  if (evidence.diffstat) {
    const { base, head, filesChanged, insertions, deletions } = evidence.diffstat;
    const sentence = `${base}..${head} is ${filesChanged} file${filesChanged === 1 ? '' : 's'} changed, ` +
      `${insertions} insertion${insertions === 1 ? '' : 's'} and ${deletions} deletion${deletions === 1 ? '' : 's'}.`;
    facts.push({ kind: 'diffstat', base, head, filesChanged, insertions, deletions, sentence });
  }

  for (const c of evidence.commits ?? []) {
    const sentence = c.inLog
      ? `commit ${c.hash} IS in the log of branch ${c.branch}.`
      : `commit ${c.hash} is NOT found — it is not a commit object in this repository.`;
    facts.push({ kind: 'commit', hash: c.hash, branch: c.branch, inLog: c.inLog, sentence });
  }

  for (const b of evidence.branches ?? []) {
    const sentence = b.exists ? `branch ${b.name} exists.` : `there is NO branch named ${b.name} in this repository.`;
    facts.push({ kind: 'branch', name: b.name, exists: b.exists, sentence });
  }

  if (evidence.tests) {
    const summary = testSummaryLines(evidence.tests.output);
    const { collected, passed, failed } = parseTestCounts(summary);
    const sentence = collected !== null || passed !== null
      ? `${collected ?? '?'} collected, ${passed ?? '?'} passed, ${failed ?? 0} failed. ` +
        `That is the whole count, from the run of \`${evidence.tests.command}\`. ` +
        'These are the ONLY test counts in this evidence.'
      : `the run of \`${evidence.tests.command}\` printed no line that looks like a test-count summary; ` +
        'no test count can be derived from it.';
    facts.push({ kind: 'tests', command: evidence.tests.command, collected, passed, failed, sentence });
  }

  for (const pr of evidence.prs ?? []) {
    if (pr.state) {
      const { state, isDraft, headRefName, mergedAt } = pr.state;
      const sentence = `pull request #${pr.number}: state ${state}, ${isDraft ? 'a draft' : 'not a draft'}` +
        (headRefName ? `, head branch ${headRefName}` : '') +
        (mergedAt ? `, merged at ${mergedAt}` : ', not merged') + '.';
      facts.push({
        kind: 'pr-state', number: pr.number, state, isDraft,
        headRefName: headRefName ?? null, mergedAt: mergedAt ?? null, sentence
      });
    }
    if (pr.checks) {
      const failing = pr.checks.filter(c => !/success|pass|neutral|skipped/i.test(c.state)).map(c => c.name);
      const sentence = pr.checks.length === 0
        ? `pull request #${pr.number}: no checks reported.`
        : failing.length === 0
          ? `pull request #${pr.number} checks: ${pr.checks.length} reported, and every one of them passes.`
          : `pull request #${pr.number} checks: ${pr.checks.length} reported, NOT all pass — failing: ${failing.join(', ')}.`;
      facts.push({
        kind: 'pr-checks', number: pr.number, total: pr.checks.length,
        passing: pr.checks.length - failing.length, failing, sentence
      });
    }
  }

  // Standing, every run: everything this pattern reads is local read-only
  // command output, so a claim of a live external validation is unprovable
  // by construction — see worker-verify SKILL.md, "the derived facts come
  // FIRST".
  facts.push({
    kind: 'live-calls',
    sentence: 'NO LIVE PROVIDER CALL IS OBSERVABLE IN THIS EVIDENCE.'
  });

  return facts;
}

/** One line per fact, ready to print as the DERIVED FACTS block. */
export function formatDerivedFacts(facts: DerivedFact[]): string {
  return facts.map(f => f.sentence).join('\n');
}

// --------------------------------------------------------------- preRules

export type PreRuleVerdict = {
  claim: string;
  verdict: 'CONTRADICTED_BY_FACT';
  confidence: 1.0;
  fact: DerivedFact;
  reason: string;
};

const DELTA_WORDS = /\b(added|removed|deleted|new|extra|more|additional|fewer|introduced)\b/i;
const RUN_CUES = /\b(ran|run|passed|failed|collected|total|the (?:full|whole) (?:suite|run))\b/i;

/**
 * When a derived fact flatly contradicts a claim's own number, path, hash,
 * branch or push state, settle that claim in code, at confidence 1.00,
 * before any judge call — same contract as worker-verify's pre-rules. A rule
 * refuses to fire on a sentence it cannot read cleanly, because a wrong
 * pre-rule is an unappealable 1.00 against an honest report.
 */
export function preRules(claims: string[], facts: DerivedFact[]): PreRuleVerdict[] {
  const out: PreRuleVerdict[] = [];
  const testFact = facts.find((f): f is TestsFact => f.kind === 'tests');
  const trackedByPath = new Map(facts.filter((f): f is TrackedFact => f.kind === 'tracked').map(f => [f.path, f]));
  const lengthByPath = new Map(facts.filter((f): f is LengthFact => f.kind === 'length').map(f => [f.path, f]));
  const commitByHash = new Map(facts.filter((f): f is CommitFact => f.kind === 'commit').map(f => [f.hash, f]));
  const branchByName = new Map(facts.filter((f): f is BranchFact => f.kind === 'branch').map(f => [f.name, f]));
  const pushFact = facts.find((f): f is PushStateFact => f.kind === 'push-state');

  for (const claim of claims) {
    // 1. a claimed PASSING count, only when the sentence reads as a report of
    // a run (not a delta like "I added 3 tests"), and only when exactly one
    // "<N> pass(ed/es/ing)" phrase is present — two or more is ambiguous
    // about which number is the total, and is left to the judge, same as
    // verify.py ("two files with line counts in one sentence").
    if (testFact && testFact.passed !== null && RUN_CUES.test(claim) && !DELTA_WORDS.test(claim)) {
      const passedNums = [...claim.matchAll(/\b(\d+)\s+pass(?:ed|es|ing)?\b/gi)];
      if (passedNums.length === 1) {
        const claimed = parseInt(passedNums[0][1], 10);
        const real = testFact.passed;
        if (claimed !== real) {
          out.push({
            claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact: testFact,
            reason: `claims ${claimed} passing but the run reports ${real} passed`
          });
          continue;
        }
      }
    }

    // 2. "tracked"/"checked in"/"committed" about a path the index disagrees on.
    let settled = false;
    for (const [path, fact] of trackedByPath) {
      if (!claim.includes(path)) continue;
      const claimsTracked = /\b(tracked|checked in|committed)\b/i.test(claim) && !/\bnot\s+(?:tracked|checked in|committed)\b/i.test(claim);
      const claimsNotTracked = /\bnot\s+(?:tracked|checked in|committed)\b/i.test(claim);
      if (claimsTracked && !fact.tracked) {
        out.push({ claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact, reason: `claims ${path} is tracked, but it is not in the git index` });
        settled = true; break;
      }
      if (claimsNotTracked && fact.tracked) {
        out.push({ claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact, reason: `claims ${path} is not tracked, but it is in the git index` });
        settled = true; break;
      }
    }
    if (settled) continue;

    // 3. a path claimed to exist/contain something, but missing on disk.
    // Negation-safe: the sentence is tested with the path text removed, so a
    // file literally named "gone.py" does not read as its own denial.
    for (const [path, fact] of lengthByPath) {
      if (!claim.includes(path) || fact.exists) continue;
      const withoutPath = claim.split(path).join(' ');
      if (/\bno\s+(?:such|longer)\b|\bdoes not exist\b|\bwas removed\b|\bdeleted\b/i.test(withoutPath)) continue;
      out.push({ claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact, reason: `names ${path}, which does not exist on disk` });
      settled = true; break;
    }
    if (settled) continue;

    // 4. a commit hash the log does not contain.
    for (const [hash, fact] of commitByHash) {
      if (claim.includes(hash) && !fact.inLog) {
        out.push({ claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact, reason: `names commit ${hash}, which is not in the log` });
        settled = true; break;
      }
    }
    if (settled) continue;

    // 5. a branch name the repository does not have.
    for (const [name, fact] of branchByName) {
      if (claim.includes(name) && !fact.exists) {
        out.push({ claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact, reason: `names branch ${name}, which does not exist` });
        settled = true; break;
      }
    }
    if (settled) continue;

    // 6. "pushed" / "nothing outstanding" contradicted by real ahead-count.
    if (pushFact && /\bpush(?:ed)?\b/i.test(claim) && !/\bnot\s+push/i.test(claim) && pushFact.ahead > 0) {
      if (!/\bunpushed\b|\bnot\s+yet\s+pushed\b|\bstill\s+needs?\s+to\s+be\s+pushed\b/i.test(claim)) {
        out.push({
          claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact: pushFact,
          reason: `claims pushed, but ${pushFact.branch} has ${pushFact.ahead} commit(s) not pushed`
        });
      }
    }
  }

  return out;
}
