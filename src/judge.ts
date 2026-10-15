/** The one judge doorway for Node (Python twin: skills/super-jev/judges/__init__.py).
 * Everything that needs a judge, its key check or its retry rule comes through here; only the
 * implementation (src/jev.ts) knows the vendor's endpoint. The judge is picked by SUPERJEV_JUDGE
 * (see judge-profile.ts). Errors are typed (judge-errors.ts); any error means no verdict. */
import type { Evaluator, Request, Evaluation } from './types.ts';
import { JUDGE_PROFILE, type JudgeProfile } from './judge-profile.ts';
import { JudgeError, NoKey, Overloaded, SecretBlocked } from './judge-errors.ts';
import { Jev } from './jev.ts';
import { payloadHasSecret } from './secret-scan.ts';

export { JudgeError, NoKey, AuthRejected, Unreachable, Overloaded, BadReply, TooBig, SecretBlocked } from './judge-errors.ts';
// How many requests this process has sent in the judge's long-input mode (see jev.ts).
export { longCalls } from './jev.ts';

/** Name of the environment variable that holds the selected judge's key. */
export function keyEnv(profile: JudgeProfile = JUDGE_PROFILE): string { return profile.keyEnv; }

/** True when the selected judge's key is set (whitespace-only counts as unset); a keyless judge always is. */
export function keyPresent(env: NodeJS.ProcessEnv = process.env, profile: JudgeProfile = JUDGE_PROFILE): boolean {
  if (!profile.keyRequired) return true;
  return Boolean(env[profile.keyEnv]?.trim());
}

/** The judge's own key check. `purpose` finishes "Set <KEY> to ...". Returns quietly when the key is
 * there; else throws `fail(message)` (a CLI passes its CliError) or a NoKey. */
export function requireKey(purpose = 'use the live judge', fail?: (message: string) => Error, env: NodeJS.ProcessEnv = process.env): void {
  if (keyPresent(env)) return;
  const message = `Set ${keyEnv()} to ${purpose}`;
  throw fail ? fail(message) : new NoKey(message, `${keyEnv()} is not set`);
}

type JudgeOptions = { apiKey?: string; model?: string; fetch?: typeof fetch; profile?: JudgeProfile };
const IMPLEMENTATIONS: Record<string, (options: JudgeOptions) => Evaluator> = {
  jev: options => new Jev(options)
};

/** The selected judge, read at `profile` (default: the one SUPERJEV_JUDGE picks, at small; a context preset is a profile loaded at
 * that preset, see judge-profile.ts). `fetch` and `apiKey` are for tests. */
export function getJudge(options: JudgeOptions = {}): Evaluator {
  const kind = (options.profile ?? JUDGE_PROFILE).kind;
  const make = IMPLEMENTATIONS[kind];
  if (!make) throw new JudgeError('bad-reply', `no implementation for judge kind ${JSON.stringify(kind)}`);
  return guarded(make(options));
}

/** The door's own secret scan: no adapter is handed a request that holds a secret. */
export function guarded(inner: Evaluator): Evaluator {
  return {
    evaluate(request, signal) {
      if (payloadHasSecret(request)) throw new SecretBlocked('the request contains a secret; not sent', 'request contains a secret; not sent');
      return inner.evaluate(request, signal);
    }
  };
}

/** The one retry rule: an Overloaded answer is retried, profile.retryAttempts in all, the first wait
 * profile.retryFirstDelayMs, doubling. Nothing else is ever retried. An abort ends it at once. */
export async function retryOverloaded(run: () => Promise<Evaluation>, signal: AbortSignal, profile: JudgeProfile = JUDGE_PROFILE): Promise<Evaluation> {
  for (let attempt = 1, delay = profile.retryFirstDelayMs; ; attempt++, delay *= 2) {
    try { return await run(); }
    catch (error) {
      if (attempt >= profile.retryAttempts || signal.aborted || !(error instanceof Overloaded)) throw error;
      await new Promise(resolve => {
        const timer = setTimeout(resolve, delay);
        signal.addEventListener('abort', () => { clearTimeout(timer); resolve(undefined); }, { once: true });
      });
      if (signal.aborted) throw error;
    }
  }
}

export type { Request };
