/** Typed judge errors. The same seven mean the same thing for every judge (Python twin:
 * skills/super-jev/judges/errors.py). Every one is a call that produced NO VERDICT; none is
 * ever a pass. `reason` is a short, content-free cause safe to print (navigation shows it). */
export type JudgeErrorKind =
  'no-key' | 'auth-rejected' | 'unreachable' | 'overloaded' | 'bad-reply' | 'too-big' | 'secret-blocked';

export class JudgeError extends Error {
  readonly kind: JudgeErrorKind;
  readonly reason: string;
  constructor(kind: JudgeErrorKind, message: string, reason: string = message) {
    super(message);
    this.kind = kind;
    this.reason = reason;
  }
}
/** The judge's API key is not in the environment. */
export class NoKey extends JudgeError { constructor(message: string, reason?: string) { super('no-key', message, reason); } }
/** The judge refused the key. */
export class AuthRejected extends JudgeError { constructor(message: string, reason?: string) { super('auth-rejected', message, reason); } }
/** No answer from the network. */
export class Unreachable extends JudgeError { constructor(message: string, reason?: string) { super('unreachable', message, reason); } }
/** The judge asked to try again later: the one retryable kind. */
export class Overloaded extends JudgeError { constructor(message: string, reason?: string) { super('overloaded', message, reason); } }
/** The judge answered, but not with a usable answer. */
export class BadReply extends JudgeError { constructor(message: string, reason?: string) { super('bad-reply', message, reason); } }
/** The request is over the judge's window. */
export class TooBig extends JudgeError { constructor(message: string, reason?: string) { super('too-big', message, reason); } }
/** The request held a secret, so it was never sent. */
export class SecretBlocked extends JudgeError { constructor(message: string, reason?: string) { super('secret-blocked', message, reason); } }

export const JUDGE_ERROR_KINDS = [NoKey, AuthRejected, Unreachable, Overloaded, BadReply, TooBig, SecretBlocked] as const;
