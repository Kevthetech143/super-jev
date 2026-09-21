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
  /** Read off the gate's own evidence WINDOW plus the draft — see
   * `windowFacts` at the bottom of this file. First in the block, because a
   * window fact is what settles a claim the raw column dump only implies. */
  | WindowFact
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
  /** The gate's own assembled evidence window plus the draft being judged —
   * raw tool output, not structured atoms. Read by `windowFacts`. */
  window?: { text: string; draft: string };
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

  // Window facts come FIRST: they are the ones that settle a claim the raw
  // evidence only implies, and the whole point of the block is that a judge
  // reads them before it reads the column dump they were derived from.
  if (evidence.window) {
    facts.push(...windowFacts(evidence.window.text, evidence.window.draft));
  }

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
const PR_NUM_IN_CLAIM = /\bPR\s*#(\d+)\b/i;
const MERGED_CLAIM_WORD = /\bmerged\b/i;
const CHECKS_GREEN_CLAIM = /\bchecks?\s+(?:are\s+|all\s+)?(?:green|pass(?:ed|ing)?)\b|\ball\s+checks?\s+(?:are\s+)?(?:green|pass(?:ed|ing)?)\b/i;

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
  const prStateByNumber = new Map(facts.filter((f): f is PrStateFact => f.kind === 'pr-state').map(f => [f.number, f]));
  const prChecksByNumber = new Map(facts.filter((f): f is PrChecksFact => f.kind === 'pr-checks').map(f => [f.number, f]));

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

    // 7/8. "PR #N ... merged" or "PR #N ... checks green" contradicted by
    // the PR's own state/checks facts (from `gh pr view`/`gh pr checks`,
    // see superjev.py's _gh_pr_evidence). Only fires when the claim names
    // the same PR number a fact was actually gathered for — a claim about
    // an unrelated PR number is left to the judge, same discipline as the
    // path/hash/branch rules above.
    const prNumMatch = claim.match(PR_NUM_IN_CLAIM);
    if (prNumMatch) {
      const n = parseInt(prNumMatch[1], 10);
      const stateFact = prStateByNumber.get(n);
      if (stateFact && MERGED_CLAIM_WORD.test(claim) && stateFact.state.toUpperCase() !== 'MERGED') {
        out.push({
          claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact: stateFact,
          reason: `claims PR #${n} is merged, but its state is ${stateFact.state}`
        });
        continue;
      }
      const checksFact = prChecksByNumber.get(n);
      if (checksFact && CHECKS_GREEN_CLAIM.test(claim) && checksFact.failing.length > 0) {
        out.push({
          claim, verdict: 'CONTRADICTED_BY_FACT', confidence: 1.0, fact: checksFact,
          reason: `claims PR #${n} checks are green, but failing: ${checksFact.failing.join(', ')}`
        });
        continue;
      }
    }
  }

  return out;
}

// --------------------------------------------------------------- window facts
//
// The four families above read STRUCTURED evidence a harness gathered by
// running commands. These next four read the gate's own assembled evidence
// WINDOW — the raw tool output a Stop-hook gate already puts in front of the
// judge — plus the draft being judged, and say in a sentence what the window
// only shows as a column.
//
// Why, concretely (2026-09-17 gate analysis, LAST-MISSES.md): the gate's
// remaining misses were all the same defect. A draft claimed it had deleted a
// card while a post-write listing row in the same window still showed that
// card, and the only removal receipt named a different one. A draft claimed a
// card "rides every turn" while the window's last line read
// `untagged -> role-default (250,000 tok ~ every 152t)`. A draft made a
// universal claim over a mismatch table whose rows were all STRICTER than
// expected, which is what made the claim true rather than false. Judges catch
// "evidence says X, draft says not-X"; they are weak on reading a column dump
// as authoritative state, and on noticing that a receipt is ABSENT.
//
// Everything here is literal string and integer work, pure and offline, over
// text the caller already has. `skills/super-jev/superjev.py` carries a
// pure-Python mirror of this half (the Stop hook cannot afford a whole node
// process's startup per turn, and on a fleet install this repo is not on disk
// next to the skill at all); the two are pinned to the same fact sentences by
// fixtures in test/enhance/derive-facts.test.ts and
// skills/super-jev/tests/test_superjev.py.

export type WindowFact = {
  kind: 'window';
  family: 'delete-claim' | 'cadence' | 'result-table' | 'merge-claim';
  sentence: string;
};

export const DERIVED_FACTS_CAP = 24;

const QUAL_ID = /\b([A-Za-z][\w.\-]*(?:::[\w.\-]+)+)/g;
const SECTION_HEADER = /^\[(?:current turn|current turn reports|previous turn -\d+|session receipts)\]$/;
const SECTION_SEPARATOR = /^\s*(?:={3,}|-{3,})\s*$/;
const REMOVAL_RECEIPT = /^\s*(REMOVED|DELETED|DROPPED)\b\s*:?\s*(.*)$/;
const DRAFT_REMOVAL = /\b(?:deleted|deleting|removed|removing|dropped|dropping|(?:is|are|was|were)\s+gone|got\s+rid\s+of)\b/i;
const CADENCE = /(?:bus-\d+|untagged\s*->\s*[\w.\-]+)\s*\([^)\n]*\)/;
const EVERY_NT = /every\s+([\d,]+)\s*t\b/i;
const DRAFT_CADENCE = /\bevery\s+(?:turn|prompt|message|call)\b|\bevery\s+[\d,]+\s*(?:t\b|turns?\b)/i;
const UNIVERSAL = /\b(?:every|all|each)\b/i;
const N_OF_M = /\b(\d+)\s*(?:\/|\s+of\s+)\s*(\d+)\s+([A-Za-z][\w]*(?:[ _-][a-z][\w]*){0,2})/g;
const TUPLE_ROW = /\(\s*'([^']{1,60})'\s*,\s*'([^']{1,40})'\s*,\s*'([^']{1,40})'\s*\)/g;
const MERGE_RECEIPT = /^\s*MERGED\b|\bgh\s+pr\s+merge\s+\d+|"mergedAt"\s*:\s*"[^"]+"/i;
const PR_NUM: RegExp[] = [
  /\bgh\s+pr\s+merge\s+(\d+)/,
  /\bgh\s+pr\s+(?:view|checks)\s+(\d+)/,
  /\(#(\d+)\)/,
  /#(\d+)\b/,
  /"number"\s*:\s*(\d+)/
];
const DRAFT_MERGE: RegExp[] = [
  /\bPR\s*#(\d+)\b[^.\n]{0,40}?\bmerged\b/gi,
  /\bmerged\b[^.\n]{0,40}?\bPR\s*#(\d+)/gi,
  /#(\d+)\b[^.\n]{0,20}?\bis\s+merged\b/gi
];
/** Words that make a draft's "... merged ... PR #N" a DISJUNCTION or a
 * NEGATION rather than a claim that PR #N is merged. "either merged or on
 * PR #3" says the opposite about #3, and the family read it as a claim
 * that #3 was merged and then reported the absent receipt as a finding
 * (measured on the gate bench's t20, 2026-09-18). */
const MERGE_CLAIM_BREAK =
  /\b(?:or|nor|not|unmerged|open|pending|awaiting|instead|except|besides|rather\s+than|other\s+than)\b/i;
/** A draft clause claiming an AGGREGATE number of merges ("19 PRs merged",
 * "three pull requests merged"), or asserting one over a whole set without
 * naming any PR. Neither shape can be settled by a window — see
 * `mergeCountFacts`. */
const DRAFT_MERGE_TOTAL: RegExp[] = [
  /\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:PRs?|pull\s+requests?)\b[^.\n]{0,40}?\bmerged\b/gi,
  /\bmerged\b[^.\n]{0,40}?\b(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:PRs?|pull\s+requests?)\b/gi
];
const DRAFT_MERGE_UNIVERSAL =
  /\b(?:every|all|each|both)\b[^.\n]{0,70}?\bmerged\b|\bmerged\b[^.\n]{0,40}?\b(?:everything|all of (?:it|them))\b/i;
const WORD_NUMBERS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6,
  seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12
};
/** How strict an outcome label is, for reading a mismatch row as stricter or
 * more permissive than expected. An unknown label ranks `null` and is not
 * read — a row we cannot rank is left out rather than guessed at. */
const STRICTNESS: Record<string, number> = {
  safe_to_auto: 0, safe: 0, auto: 0, allow: 0, pass: 0,
  needs_approval: 1, approval: 1, ask: 1, confirm: 1,
  refuse: 2, refused: 2, block: 2, blocked: 2, deny: 2
};

/** The draft split into clause-sized units, mirroring superjev.py's
 * `presplit_claims` split (sentence boundaries, `;`, `: `, standalone
 * ` and `) — the split that isolates a trailing "and I also deleted X"
 * clause from the true clauses it rides on. */
export function draftClauses(draft: string): string[] {
  const text = (draft || '').trim();
  if (!text) return [];
  const units = text.split(/(?<=[.!?])\s+|;\s*|:\s+(?=\S)|\s+and\s+/).map(u => u.trim());
  const out: string[] = [];
  const seen = new Set<string>();
  for (const u of units) {
    if (!u) continue;
    const key = u.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(u);
    if (out.length >= 25) break;
  }
  return out;
}

type WindowLine = { label: string; line: string };

/** Every line of an assembled window as `{label, line}`, where the label is
 * the `[current turn]` / `[session receipts]` / `[previous turn -N]` header
 * the line sits under. A fact cites that label as its source, so a reader can
 * find the line it was read from. */
export function windowLines(windowText: string): WindowLine[] {
  let label = 'the evidence window';
  const out: WindowLine[] = [];
  for (const raw of (windowText || '').split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (SECTION_HEADER.test(line.trim())) { label = line.trim(); continue; }
    if (!line.trim() || SECTION_SEPARATOR.test(line)) continue;
    out.push({ label, line });
  }
  return out;
}

function idsIn(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of (text || '').matchAll(QUAL_ID)) {
    const ident = m[1].replace(/[.,;:!?)'"]+$/, '');
    if (ident && !seen.has(ident)) { seen.add(ident); out.push(ident); }
  }
  return out;
}

/** True when `text` names `ident` — in full, or by its last `::` segment on
 * word boundaries (a draft says `fable_awareness` where the window says
 * `agent_role::fable_awareness`). Word-bounded, so a longer name that merely
 * contains this one does not count. */
function mentionsId(text: string, ident: string): boolean {
  if (!text) return false;
  if (text.includes(ident)) return true;
  const tail = ident.split('::').pop() as string;
  if (tail.length < 4) return false;
  const esc = tail.replace(/[.*+?^${}()|[\]\\\-]/g, '\\$&');
  return new RegExp(`(?<![\\w.:\\-])${esc}(?![\\w.\\-])`).test(text);
}

/** True when `line` reads as a LISTING row for `ident`: the identifier
 * followed by at least two numeric columns, which is the shape `card.py` and
 * friends print state in. A listing row emitted after a claimed removal is a
 * statement that the thing is still there. */
function isListingLine(line: string, ident: string): boolean {
  const idx = line.indexOf(ident);
  if (idx < 0) return false;
  if (REMOVAL_RECEIPT.test(line)) return false;
  const rest = line.slice(idx + ident.length);
  return (rest.match(/\b[\d,]+\b/g) || []).length >= 2;
}

function strictness(label: string): number | null {
  const v = STRICTNESS[(label || '').trim().toLowerCase()];
  return v === undefined ? null : v;
}

/** Family 1 — delete/remove claims. Every removal RECEIPT in the window is
 * stated as a fact, and any identifier the draft claims to have removed that
 * instead appears on a LISTING row is stated as still present. The lie the
 * bench missed is exactly the gap between those halves. */
function deleteClaimFacts(lines: WindowLine[], clauses: string[]): WindowFact[] {
  const facts: WindowFact[] = [];
  const removed = new Map<string, string>();
  for (const { label, line } of lines) {
    const m = line.match(REMOVAL_RECEIPT);
    if (!m) continue;
    for (const ident of idsIn(m[2])) if (!removed.has(ident)) removed.set(ident, label);
  }
  for (const [ident, label] of removed) {
    facts.push({ kind: 'window', family: 'delete-claim', sentence: `${ident} removed per ${label}.` });
  }
  const claimed: string[] = [];
  for (const clause of clauses) {
    if (!DRAFT_REMOVAL.test(clause)) continue;
    for (const { line } of lines) {
      for (const ident of idsIn(line)) {
        if (claimed.includes(ident) || removed.has(ident)) continue;
        if (mentionsId(clause, ident)) claimed.push(ident);
      }
    }
  }
  for (const ident of claimed) {
    for (const { label, line } of lines) {
      if (isListingLine(line, ident)) {
        facts.push({
          kind: 'window', family: 'delete-claim',
          sentence: `${ident} still present in ${label} after the claimed removal.`
        });
        break;
      }
    }
  }
  return facts;
}

/** Family 2 — cadence claims. When the draft asserts an injection frequency,
 * quote the window's own cadence line for each card the draft names and do the
 * one integer comparison the judge did not: `~ every 152t` is every 152 turns,
 * which is not every turn. */
function cadenceFacts(lines: WindowLine[], draft: string): WindowFact[] {
  if (!DRAFT_CADENCE.test(draft || '')) return [];
  const facts: WindowFact[] = [];
  const seen = new Set<string>();
  for (const { label, line } of lines) {
    const m = line.match(CADENCE);
    if (!m) continue;
    const expr = m[0].split(/\s+/).join(' ');
    for (const ident of idsIn(line)) {
      if (seen.has(ident) || !mentionsId(draft, ident)) continue;
      seen.add(ident);
      const n = expr.match(EVERY_NT);
      let tail = '.';
      if (n) {
        const num = parseInt(n[1].replace(/,/g, ''), 10);
        if (Number.isFinite(num) && num > 1) tail = ` — that is every ${num} turns, not every turn.`;
        else if (num === 1) tail = ' — that is every turn.';
      }
      facts.push({ kind: 'window', family: 'cadence', sentence: `${ident} cadence in ${label}: "${expr}"${tail}` });
    }
  }
  return facts;
}

/** Family 3 — pass/mismatch tables. When the window holds a results table and
 * the draft makes a universal claim about that population, print the counts,
 * so the judge compares the claim against a number instead of eyeballing
 * rows. */
function resultTableFacts(lines: WindowLine[], draft: string): WindowFact[] {
  const nOfM: [number, number, string][] = [];
  const rows: [string, string, string][] = [];
  for (const { line } of lines) {
    for (const m of line.matchAll(N_OF_M)) {
      nOfM.push([parseInt(m[1], 10), parseInt(m[2], 10), m[3].split(/\s+/).join(' ').toLowerCase()]);
    }
    for (const m of line.matchAll(TUPLE_ROW)) rows.push([m[1], m[2], m[3]]);
  }
  if (!nOfM.length && !rows.length) return [];
  if (!UNIVERSAL.test(draft || '')) return [];
  const labels = new Set<string>(nOfM.map(([, , l]) => l));
  for (const [, e, a] of rows) { labels.add(e.toLowerCase()); labels.add(a.toLowerCase()); }
  const draftLow = (draft || '').toLowerCase();
  const words = new Set<string>();
  for (const l of labels) for (const w of l.split(/[ _-]+/)) if (w.length >= 4) words.add(w);
  if (![...words].some(w => draftLow.includes(w))) return [];

  const facts: WindowFact[] = [];
  const seen = new Set<string>();
  for (const [n, m, label] of nOfM) {
    const sentence = `table shows ${n} of ${m} ${label}.`;
    if (seen.has(sentence)) continue;
    seen.add(sentence);
    facts.push({ kind: 'window', family: 'result-table', sentence });
  }
  if (rows.length) {
    const byActual = new Map<string, number>();
    for (const [, , act] of rows) byActual.set(act, (byActual.get(act) ?? 0) + 1);
    const actuals = [...byActual.keys()].sort((a, b) => (byActual.get(b)! - byActual.get(a)!) || a.localeCompare(b));
    for (const act of actuals) {
      facts.push({
        kind: 'window', family: 'result-table',
        sentence: `table shows ${byActual.get(act)} of ${rows.length} mismatch rows with actual ${act}.`
      });
    }
    const ranked = rows.map(([, e, a]) => [strictness(e), strictness(a)] as [number | null, number | null]);
    if (ranked.every(([e, a]) => e !== null && a !== null)) {
      const stricter = ranked.filter(([e, a]) => (a as number) > (e as number)).length;
      const looser = ranked.filter(([e, a]) => (a as number) < (e as number)).length;
      facts.push({
        kind: 'window', family: 'result-table',
        sentence: `table shows ${stricter} of ${rows.length} mismatch rows stricter than expected, ${looser} more permissive.`
      });
    }
  }
  return facts;
}

/** Family 4 — merge/CI claims. Every merge receipt in the window is named
 * with its PR number, and every PR the draft says was merged with no such
 * receipt is named as missing one — the absent half a judge does not do. */
function mergeClaimFacts(lines: WindowLine[], draft: string): WindowFact[] {
  const found = new Map<number, string>();
  for (const { label, line } of lines) {
    if (!MERGE_RECEIPT.test(line)) continue;
    for (const rx of PR_NUM) {
      const m = line.match(rx);
      if (m) { if (!found.has(parseInt(m[1], 10))) found.set(parseInt(m[1], 10), label); break; }
    }
  }
  const facts: WindowFact[] = [...found.keys()].sort((a, b) => a - b).map(n => ({
    kind: 'window' as const, family: 'merge-claim' as const,
    sentence: `merge receipt found for PR #${n} in ${found.get(n)}.`
  }));
  const claimed = new Set<number>();
  for (const rx of DRAFT_MERGE) {
    for (const m of (draft || '').matchAll(rx)) {
      // A match whose own gap between "merged" and the PR number carries a
      // disjunction or a negation is not a claim that the PR is merged.
      if (MERGE_CLAIM_BREAK.test(m[0].replace(m[1], ' '))) continue;
      claimed.add(parseInt(m[1], 10));
    }
  }
  for (const n of [...claimed].sort((a, b) => a - b)) {
    if (found.has(n)) continue;
    facts.push({ kind: 'window', family: 'merge-claim', sentence: `no merge receipt for PR #${n} in window.` });
  }
  facts.push(...mergeCountFacts(found, draft));
  return facts;
}

/**
 * Family 4b — a draft claim about the SESSION'S merge total, stated as a
 * count the window cannot hold.
 *
 * A gate window is one session's worth of receipts under a byte cap. A
 * draft that says "19 PRs merged" or "every item is merged" is making a
 * claim whose scope is the whole session, and a window that saturates well
 * below that number cannot settle it either way. Left unsaid, the judge
 * reads the gap between the claimed total and the receipt count as a
 * contradiction and blocks a true reply. So the count is stated together
 * with what it does and does not settle: this is an UNVERIFIABLE-here
 * claim, not a false one.
 *
 * Fires only when the draft's own total exceeds the window's receipt count,
 * or when the claim names no total at all — a total the window already
 * matches is checkable, and family 4 has already checked it.
 *
 * The sentence quotes the draft's own claimed total, not just the window's
 * count, and when the window holds ZERO merge receipts it says so directly
 * rather than a bare "0" a reader could skim past — a claimed total against
 * zero corroboration should read as "not checked at all", not as amnesty
 * for the claim (2026-09-18, judge-safety review).
 */
function mergeCountFacts(found: Map<number, string>, draft: string): WindowFact[] {
  let claimedTotal: number | null = null;
  for (const rx of DRAFT_MERGE_TOTAL) {
    for (const m of (draft || '').matchAll(rx)) {
      const tok = (m[1] || '').toLowerCase();
      const val = WORD_NUMBERS[tok] ?? (/^\d+$/.test(tok) ? parseInt(tok, 10) : null);
      if (val === null) continue;
      if (claimedTotal === null || val > claimedTotal) claimedTotal = val;
    }
  }
  const universal = DRAFT_MERGE_UNIVERSAL.test(draft || '');
  if (claimedTotal === null && !universal) return [];
  if (claimedTotal !== null && claimedTotal <= found.size) return [];
  const claimDesc = claimedTotal !== null
    ? `the draft claims ${claimedTotal} merged`
    : 'the draft claims every item merged';
  if (found.size === 0) {
    return [{
      kind: 'window', family: 'merge-claim',
      sentence: `merge receipts in window: 0; ${claimDesc}; the window carries `
        + `no merge receipt at all, so this claim is unsupported here.`
    }];
  }
  return [{
    kind: 'window', family: 'merge-claim',
    sentence: `merge receipts in window: ${found.size}; ${claimDesc}; the draft's `
      + `session-wide total cannot be checked here.`
  }];
}

/**
 * The DERIVED FACTS atoms for one gate window, in block order: delete/remove
 * claims, cadence claims, result tables, merge/CI claims. Pure and offline —
 * literal string and integer work over `windowText` and `draftText`, deduped
 * by sentence and capped at `DERIVED_FACTS_CAP`. Returns `[]` when nothing is
 * derivable, which is the common case.
 */
export function windowFacts(windowText: string, draftText: string): WindowFact[] {
  const lines = windowLines(windowText);
  if (!lines.length) return [];
  const draft = draftText || '';
  const clauses = draftClauses(draft);
  const all = [
    ...deleteClaimFacts(lines, clauses),
    ...cadenceFacts(lines, draft),
    ...resultTableFacts(lines, draft),
    ...mergeClaimFacts(lines, draft)
  ];
  const out: WindowFact[] = [];
  const seen = new Set<string>();
  for (const f of all) {
    if (seen.has(f.sentence)) continue;
    seen.add(f.sentence);
    out.push(f);
    if (out.length >= DERIVED_FACTS_CAP) break;
  }
  return out;
}
