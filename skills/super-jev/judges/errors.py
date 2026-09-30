"""Typed judge errors. The same seven mean the same thing for every judge.

Every one is a call that produced NO VERDICT. None is ever a pass: a caller that
catches one treats the check as not done, never as clean.

    NoKey           the judge's API key is not in the environment
    AuthRejected    the judge refused the key
    Unreachable     no answer from the network
    Overloaded      the judge asked to try again later (the one retryable kind)
    BadReply        the judge answered, but not with a usable answer
    TooBig          the request is over the judge's window
    SecretBlocked   the request held a secret, so it was never sent

`JudgeError` itself is the base, and also what a plain usage error raises
(no evidence file, no checkable claim).
"""

__all__ = ["JudgeError", "NoKey", "AuthRejected", "Unreachable", "Overloaded",
           "BadReply", "TooBig", "SecretBlocked", "ERROR_KINDS"]


class JudgeError(RuntimeError):
    """A call that produced no verdict. Always exit 1, never a pass."""
    kind = "error"


class NoKey(JudgeError):
    kind = "no-key"


class AuthRejected(JudgeError):
    kind = "auth-rejected"


class Unreachable(JudgeError):
    kind = "unreachable"


class Overloaded(JudgeError):
    kind = "overloaded"
    retryable = True


class BadReply(JudgeError):
    kind = "bad-reply"


class TooBig(JudgeError):
    kind = "too-big"


class SecretBlocked(JudgeError):
    kind = "secret-blocked"


ERROR_KINDS = (NoKey, AuthRejected, Unreachable, Overloaded, BadReply, TooBig, SecretBlocked)
