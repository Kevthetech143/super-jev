/**
 * Permit pre-rules: a deterministic, code-only, zero-network layer that runs
 * BEFORE `decidePermit`'s own model call (see `permit.ts`). Ported from the
 * offline experiment at
 * the original permit-facts prototype,
 * which replayed these same rules against 30 saved live permit cases plus 20
 * hand-labeled real commands with zero dangerous outcomes (a hard-rule class
 * ever letting an expected refuse/needs_approval action through as
 * safe_to_auto) before this port.
 *
 * What this adds on top of `matchIrreversibleKeywords`/`hardRuleClass`
 * (which already fold the same keyword table into refuse/needs_approval
 * before any model call):
 *
 *   1. Verb + target parsing: pull the concrete acted-upon object out of the
 *      action/target text (a path for `rm -rf`, a branch for a force push, a
 *      table for `drop table`, a disk for `format`/`mkfs`/`diskutil erase`,
 *      a dollar amount + payee) with narrow regexes, not free text.
 *   2. Reversibility facts, read live off the filesystem and git when a
 *      `--cwd` is given: does the target exist, is it tracked in git, is
 *      there a backup sibling, is the branch main/protected, is the
 *      resolved path inside the task's own worktree. Without `--cwd` these
 *      stay `null` (unknown) rather than a guessed `false`.
 *   3. Two rules `matchIrreversibleKeywords` alone cannot express because
 *      they need those facts, not just the text:
 *        - money above a configurable threshold (default $200, the
 *          risk-scaled payments rule already in play elsewhere in this
 *          fleet) or a brand-new payee escalates to `needs_approval`
 *          regardless of what the judge would have said — no judge call.
 *        - a destructive verb whose target is tracked and committed in git
 *          inside the task's own worktree is recoverable from the index, so
 *          instead of refusing outright it is passed through to the judge
 *          with that fact attached, rather than blocked sight unseen.
 *
 * Every other destructive match, with no such carve-out, refuses outright
 * with no judge call, same as `decidePermit`'s own hard rule today.
 *
 * IMPORTANT (matches the brief and the offline experiment's own framing): a
 * pre-rule may only ever make the verdict MORE cautious or leave it
 * unsettled. `decide()` below can only return `refuse`, `needs_approval` or
 * `defer_to_judge` — never `safe_to_auto`. Nothing here is a fast path to
 * "yes, go"; the fastest a pre-rule can go is "stop asking, this is
 * settled" toward refuse or needs_approval, or "no free check settled this,
 * hand it to the judge."
 */
import { existsSync, readdirSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { isAbsolute, join, dirname, basename, resolve as resolvePath } from 'node:path';
import { homedir } from 'node:os';
import { matchIrreversibleKeywords, hardRuleClass, type HardRuleClass } from './permit.ts';

export const DEFAULT_MONEY_THRESHOLD = 200;

export type PermitPreRuleVerdict = 'refuse' | 'needs_approval' | 'defer_to_judge';

export type ParsedActionObject = {
  rmRfPath?: string;
  forcePushBranch?: string;
  dropTarget?: string;
  dropKind?: string;
  formatTarget?: string;
  deleteFileCount?: number;
  amountUsd?: number;
  payee?: string;
};

export type ReversibilityFacts = {
  resolvedPath?: string | null;
  exists?: boolean | null;
  isNoopDelete?: boolean;
  inGitRepo?: boolean | null;
  gitToplevel?: string;
  gitTracked?: boolean | null;
  hasBackupSibling?: boolean | null;
  note?: string;
};

export type BranchFacts = {
  branch?: string;
  protectedByName?: boolean;
  branchExistsLocallyOrRemoteTracking?: boolean;
};

export type MoneyFacts = {
  amountUsd?: number;
  overThreshold?: boolean;
  threshold?: number;
  newPayee?: boolean;
  ruleQuoted?: string;
};

export type ScopeFacts = {
  targetPath?: string;
  worktree?: string;
  insideTaskWorktree?: boolean;
};

export type PermitPreRuleFacts = {
  matchedKeywords: string[];
  hardRuleClass?: HardRuleClass;
  parsedObject: ParsedActionObject;
  reversibility: ReversibilityFacts;
  branch: BranchFacts;
  money: MoneyFacts;
  scope: ScopeFacts;
};

export type PermitPreRuleResult = {
  verdict: PermitPreRuleVerdict;
  settledBy: string | null;
  reason: string;
  facts: PermitPreRuleFacts;
};

export type PermitPreRuleOptions = {
  cwd?: string;
  worktree?: string;
  newPayee?: boolean;
  moneyThreshold?: number;
  /** reversibilityNotes + policyLines, folded into the keyword scan only (not the verb/object parse), so a hard-rule word appearing only there still counts -- matches decidePermit's own scanText. */
  extraText?: string;
};

const MONEY_RULE_TEXT =
  'PAYMENTS RISK-SCALED (payments_risk_scaled): ' +
  'an approved payment is authorized. At/under the threshold: do it, report after. Above the ' +
  'threshold, or for a new payee / recurring or subscription charge / wire / crypto / anything ' +
  'irreversible at size: stop and confirm payee + amount + method first.';

// ---------------------------------------------------------------------------
// 1. Verb + target parse. Deliberately narrow, high-precision extractors,
//    not a general parser -- each one only fires on the exact shape named
//    in the brief. Reuses `matchIrreversibleKeywords`'s own normalization so
//    the same inflections/homoglyphs it tolerates are tolerated here too.
// ---------------------------------------------------------------------------

function norm(text: string): string {
  return text
    .normalize('NFKC')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

export function parseActionObject(action: string, target?: string): ParsedActionObject {
  const n = norm(action);
  const out: ParsedActionObject = {};

  let m = n.match(/\brm\s+-[a-z]*(rf|fr)[a-z]*\s+(?:the\s+)?(\S+)/);
  if (m) out.rmRfPath = target || m[2];

  m = n.match(/\bgit\s+push\b.*?(?:-f|--force)\b.*?\b([\w./-]+)$/);
  if (m) out.forcePushBranch = target || m[1];

  m = n.match(/\bdrop\s+(table|database|column)\s+(?:the\s+)?([\w.]+)/);
  if (m) { out.dropTarget = target || m[2]; out.dropKind = m[1]; }

  m = n.match(/\bformat\s+(?:the\s+)?([\w./-]+)/);
  if (m && /disk|drive|volume|partition|sd\s*card|usb/.test(n)) out.formatTarget = target || m[1];

  m = n.match(/\bdelete\s+(\d+)\s+files?\b/);
  if (m) out.deleteFileCount = Number(m[1]);

  m = action.match(/\$(\d+(?:\.\d+)?)/);
  if (m) out.amountUsd = Number(m[1]);

  m = n.match(/\bto\s+([\w.@-]+)\s*$/);
  if (m && (out.amountUsd !== undefined || /\bpay|wire|transfer|settle\b/.test(n))) out.payee = target || m[1];

  return out;
}

// ---------------------------------------------------------------------------
// 2. Reversibility + branch + scope facts, checked against real state when
//    a --cwd/--worktree is given. Every fact is a plain filesystem or git
//    read -- no judge, no network, ever. Without --cwd, facts stay
//    undefined/null (unknown) rather than a guessed false: this mirrors the
//    Python original's explicit refusal to guess.
// ---------------------------------------------------------------------------

function runGit(args: string[], cwd: string): { code: number; out: string } {
  try {
    const p = spawnSync('git', args, { cwd, timeout: 5000, encoding: 'utf8' });
    const out = (p.stdout || p.stderr || '').trim();
    return { code: p.status ?? 1, out };
  } catch {
    return { code: 1, out: '' };
  }
}

function expandHome(p: string): string {
  if (p === '~') return homedir();
  if (p.startsWith('~/')) return join(homedir(), p.slice(2));
  return p;
}

export function reversibilityFacts(targetPath: string | undefined, cwd: string | undefined): ReversibilityFacts {
  if (!targetPath) {
    return { note: 'no concrete path in the action/target text; no filesystem/git fact available' };
  }
  if (!cwd) {
    // No real directory to check against: every fact below is a live
    // machine read, so without a --cwd we honestly don't know and must not
    // guess. Facts stay null (unknown), never silently false/true.
    return {
      resolvedPath: null, exists: null, isNoopDelete: false,
      inGitRepo: null, gitTracked: null, hasBackupSibling: null,
      note: 'no --cwd given; filesystem/git facts unverifiable, treated as unknown'
    };
  }
  const expanded = expandHome(targetPath);
  const resolved = isAbsolute(expanded) ? expanded : join(cwd, expanded);
  const facts: ReversibilityFacts = { resolvedPath: resolved };
  facts.exists = existsSync(resolved);
  facts.isNoopDelete = !facts.exists;

  let checkDir: string;
  try {
    checkDir = statSync(resolved).isDirectory() ? resolved : (dirname(resolved) || cwd);
  } catch {
    checkDir = dirname(resolved) || cwd;
  }
  const top = runGit(['rev-parse', '--show-toplevel'], checkDir);
  facts.inGitRepo = top.code === 0;
  if (top.code === 0) {
    facts.gitToplevel = top.out;
    // Pass the absolute resolved path straight to `git ls-files` rather than
    // computing a relative path by hand: on macOS, `--cwd` is often under
    // /var while `git rev-parse --show-toplevel` resolves the /private/var
    // symlink, so `relative(toplevel, resolved)` produces a bogus ../..
    // path even though both point at the same file. Git resolves an
    // absolute path against its own toplevel correctly either way.
    const tracked = runGit(['ls-files', '--error-unmatch', resolved], checkDir);
    facts.gitTracked = tracked.code === 0;
  } else {
    facts.gitTracked = false;
  }

  const parent = dirname(resolved) || cwd;
  let siblings: string[] = [];
  try { if (statSync(parent).isDirectory()) siblings = readdirSync(parent); } catch { /* not a dir / missing */ }
  const base = basename(resolved);
  facts.hasBackupSibling =
    ['.bak', '.backup', '~'].some(suffix => existsSync(resolved + suffix)) ||
    siblings.some(f => f.startsWith(base + '.bak'));

  return facts;
}

export function branchFacts(branch: string | undefined, cwd: string | undefined): BranchFacts {
  if (!branch) return {};
  const trimmed = branch.trim().replace(/\.+$/, '');
  const lastSeg = trimmed.split('/').pop() ?? trimmed;
  const protectedByName =
    lastSeg === 'main' || lastSeg === 'master' ||
    trimmed.replace(/\/+$/, '').endsWith('/main') || trimmed.replace(/\/+$/, '').endsWith('/master');
  const facts: BranchFacts = { branch, protectedByName };
  if (cwd) {
    const rc = runGit(['rev-parse', '--verify', branch], cwd);
    facts.branchExistsLocallyOrRemoteTracking = rc.code === 0;
  }
  return facts;
}

export function moneyFacts(amount: number | undefined, newPayee: boolean | undefined, threshold: number): MoneyFacts {
  if (amount === undefined) return {};
  return {
    amountUsd: amount,
    overThreshold: amount > threshold,
    threshold,
    newPayee: Boolean(newPayee),
    ruleQuoted: MONEY_RULE_TEXT
  };
}

export function scopeFacts(targetPath: string | undefined, worktree: string | undefined): ScopeFacts {
  if (!targetPath || !worktree) return {};
  const resolved = resolvePath(expandHome(targetPath));
  const wt = resolvePath(expandHome(worktree));
  const inside = resolved === wt || resolved.startsWith(wt + '/');
  return { targetPath: resolved, worktree: wt, insideTaskWorktree: inside };
}

// ---------------------------------------------------------------------------
// 3. Fold the facts above into a verdict, or defer to the judge. Never
//    returns safe_to_auto: only refuse, needs_approval, or defer_to_judge.
// ---------------------------------------------------------------------------

export function decide(action: string, target: string | undefined, options: PermitPreRuleOptions = {}): PermitPreRuleResult {
  const { cwd, worktree, newPayee, moneyThreshold = DEFAULT_MONEY_THRESHOLD, extraText } = options;
  const text = [action, target, extraText].filter(Boolean).join(' ');
  const matchedKeywords = matchIrreversibleKeywords(text);
  const cls = hardRuleClass(matchedKeywords);
  const obj = parseActionObject(action, target);

  const revTargetPath = obj.rmRfPath || obj.dropTarget || target;
  const rev = reversibilityFacts(revTargetPath, cwd);
  const br = branchFacts(obj.forcePushBranch, cwd);
  const mo = moneyFacts(obj.amountUsd, newPayee, moneyThreshold);
  // Scope against the same resolved absolute path reversibilityFacts just
  // computed (when a --cwd was given to resolve it), not the raw
  // possibly-relative target text -- otherwise "tracked.txt" resolves
  // against this process's own cwd instead of the target's real location.
  const sc = scopeFacts(rev.resolvedPath ?? revTargetPath, worktree);

  const facts: PermitPreRuleFacts = {
    matchedKeywords, hardRuleClass: cls, parsedObject: obj,
    reversibility: rev, branch: br, money: mo, scope: sc
  };

  // Destructive verb whose target is tracked+committed in git AND inside
  // the task's own worktree: recoverable from the index, in scope. Not
  // settled -- passed through to the judge with that fact attached, rather
  // than refused sight unseen or (never) auto-allowed.
  const recoverableFromGit = cls === 'destructive' && rev.gitTracked === true && sc.insideTaskWorktree === true;
  if (recoverableFromGit) {
    return {
      verdict: 'defer_to_judge', settledBy: null,
      reason: 'destructive verb, but target is tracked and committed in git inside the task worktree: ' +
        'recoverable from the index; passed to the judge with that fact, not refused sight unseen and never auto-allowed',
      facts
    };
  }

  // Destructive verb, target not known to be recoverable: refuse outright,
  // no judge call. Same class/verdict `decidePermit`'s own hard rule
  // already applies, made here without a model call so the manifest shows
  // zero calls for it too.
  if (cls === 'destructive') {
    return {
      verdict: 'refuse', settledBy: 'hard_rule_destructive',
      reason: `hard rule: destructive pattern(s) ${matchedKeywords.join(', ')}; refused outright, no model call needed`,
      facts
    };
  }

  // Money above the risk-scaled threshold, or a new payee, settles the
  // question regardless of what the judge would say.
  if (mo.overThreshold || mo.newPayee) {
    return {
      verdict: 'needs_approval', settledBy: 'money_rule',
      reason: `amount $${mo.amountUsd} is over the $${moneyThreshold} risk-scaled rule, or a new payee: needs_approval regardless of judge`,
      facts
    };
  }

  return {
    verdict: 'defer_to_judge', settledBy: null,
    reason: 'no free check settled this; hand to the permit judge as before',
    facts
  };
}

/** Plain-words rendering of the derived facts, printed before the judge verdict. */
export function formatPermitPreRuleFacts(result: PermitPreRuleResult): string {
  const lines: string[] = [];
  const { facts } = result;
  lines.push(`Derived facts (pre-rules, zero network):`);
  lines.push(`  matched keywords: ${facts.matchedKeywords.length ? facts.matchedKeywords.join(', ') : '(none)'}`);
  if (facts.hardRuleClass) lines.push(`  hard-rule class: ${facts.hardRuleClass}`);
  const obj = facts.parsedObject;
  const objParts = Object.entries(obj).filter(([, v]) => v !== undefined).map(([k, v]) => `${k}=${v}`);
  if (objParts.length) lines.push(`  parsed object: ${objParts.join(', ')}`);
  if (facts.reversibility.note) lines.push(`  reversibility: ${facts.reversibility.note}`);
  else if (facts.reversibility.resolvedPath !== undefined) {
    lines.push(`  reversibility: exists=${facts.reversibility.exists} tracked=${facts.reversibility.gitTracked} backup=${facts.reversibility.hasBackupSibling}`);
  }
  if (facts.branch.branch) lines.push(`  branch: ${facts.branch.branch} protected=${facts.branch.protectedByName}`);
  if (facts.money.amountUsd !== undefined) lines.push(`  money: $${facts.money.amountUsd} overThreshold=${facts.money.overThreshold} newPayee=${facts.money.newPayee}`);
  if (facts.scope.insideTaskWorktree !== undefined) lines.push(`  scope: insideTaskWorktree=${facts.scope.insideTaskWorktree}`);
  lines.push(`  verdict: ${result.verdict}${result.settledBy ? ` (settled by ${result.settledBy})` : ' (unsettled)'}`);
  lines.push(`  reason: ${result.reason}`);
  return lines.join('\n');
}
