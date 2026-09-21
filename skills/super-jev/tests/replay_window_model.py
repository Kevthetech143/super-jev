"""Replay proof for window_model.py: BYTE-IDENTITY against the composer.

For every recorded gate-bench case, this loads the case's own transcript
(the same `payloads-v3*/<id>/transcript.jsonl` the v3 shim replays read)
and compares, offline, with no network and no TYPESAFE_API_KEY:

  1. `window_model.from_transcript(t).render()` against
     `superjev._derive_evidence_text_from_transcript(t)` — byte for byte.
  2. `window_model.from_transcript(t).meta` against the composer's own
     `meta` dict, field for field.
  3. The round-trip property `from_text(render(from_transcript(t))) ==
     from_transcript(t)` — the two constructors agreeing on PIECES, not
     just on bytes, under `from_text`'s one fail-closed parse.
  4. The SAFETY direction, on every case including the ones property 3
     does not hold for: every line `from_text` marks trusted is a line
     `from_transcript` also marks trusted, AND carries the same PIECE
     KIND. Re-parsing composed bytes may lose trust; it must never invent
     it, and it must never turn a claim into a receipt or a session
     receipt while keeping the text. On a window that took the composer's
     last-resort whole-window byte tail-keep, whole-line identity is
     meaningless — that cut lands mid-line — so there the check is that
     every trusted re-parsed line is CONTAINED in text
     `from_transcript` trusts, which is the same guarantee at the only
     granularity the bytes still support.

Property 3 is the one that legitimately fails on recorded bytes, and how
often is a measurement worth having rather than a bug. This checkout's
composer copies a worker's report body in VERBATIM, so once a report
label appears, `from_text` cannot prove that a later `===` is the
composer's section separator rather than the worker's own text, and folds
everything after it into the report. There is no flag that recovers the
exact parse on this checkout's bytes — `from_text` takes no assertion
about the composer on faith, because trusting an unverified claim about
the composer is exactly the hole it exists to close (see
`window_model.from_text`). Property 4 is what makes the loss acceptable,
and properties 1, 2 and 4 cover every case regardless.

    python3 skills/super-jev/tests/replay_window_model.py

The model is compared against the composer in its OWN directory by
default, and then, in a child process, against PR #53's composer at
`SUPERJEV_PR53_DIR` when that worktree is on this machine — the two
branches render a previous turn's relayed reports differently (PR #53
prefixes them with `[relayed reports in this turn]`) and `render()` has
to be byte-identical on both. `SUPERJEV_WM_COMPOSER_DIR` is what the
child sets; it is not something a caller needs.

Exits 0 iff every case is byte-identical and meta-identical, no line
gained trust or changed kind, and the PR #53 child (when it runs) says
the same. Prints the round-trip tally. Exits 0 with a skip note when no
bench is on this machine (the benches live outside the repo, under
~/super-jev-experiments).
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

#: Which checkout's COMPOSER to compare this checkout's model against.
#: Set by the PR #53 child run; unset means "this checkout's own".
COMPOSER_DIR = Path(os.environ.get("SUPERJEV_WM_COMPOSER_DIR") or SKILL_DIR)
#: PR #53's worktree, whose composer renders a previous turn's relayed
#: reports behind a `[relayed reports in this turn]` mark that `main` does
#: not emit. `render()` has to match both.
PR53_DIR = Path(os.environ.get("SUPERJEV_PR53_DIR") or SKILL_DIR)


def _load_superjev(directory):
    """`superjev` loaded from `directory`, under the module name
    `window_model._superjev` reuses — so the model and the composer it is
    measured against are always the same file, never one of each."""
    spec = importlib.util.spec_from_file_location(
        "superjev", Path(directory) / "superjev.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["superjev"] = mod
    spec.loader.exec_module(mod)
    return mod


sj = _load_superjev(COMPOSER_DIR)
import window_model as wm        # noqa: E402

#: True when the composer under test is this checkout's own. Byte and meta
#: identity is then required on EVERY case. Against a foreign composer it
#: is required only on windows that took no truncation: PR #53 adds
#: `_repair_report_tail`, which re-opens a fence a byte cut sliced through
#: and quotes out the fragment the cut left behind, and this model
#: reproduces `main`'s byte cuts instead. Porting that repair is the
#: migration's job, not this proof's — but which cases it accounts for has
#: to be counted rather than waved at.
OWN_COMPOSER = COMPOSER_DIR.resolve() == SKILL_DIR.resolve()

REPO_ROOT = SKILL_DIR.parent.parent
DEFAULT_ROOT = REPO_ROOT / ".local" / "recorded-benches"
#: The recorded gate benches, oldest first.
BENCHES = (
    ("gate-bench-20260917", "payloads-v3"),
    ("gate-bench-20260918", "payloads-v3-2"),
    ("gate-bench-20260918-fleet", "payloads-v3-3"),
)


def cases(root):
    for bench, payload_dir in BENCHES:
        base = Path(root) / bench / payload_dir
        if not base.is_dir():
            continue
        for case_dir in sorted(base.iterdir()):
            transcript = case_dir / "transcript.jsonl"
            payload = case_dir / "payload.json"
            if transcript.is_file():
                draft = ""
                if payload.is_file():
                    try:
                        draft = json.loads(payload.read_text(encoding="utf-8")).get(
                            "last_assistant_message", "") or ""
                    except (ValueError, OSError):
                        draft = ""
                yield f"{bench}/{case_dir.name}", str(transcript), draft


def _trusted_lines(window):
    """{(piece kind, stripped line text): count} over every TRUSTED
    content line.

    Keyed by the KIND as well as the text, so the check below is the real
    one: a line may not gain trust, and it may not keep its text while
    changing from a claim into a receipt or a session-receipt fact. Text
    alone would have passed a re-parse that turned a worker's
    `MERGED PR #52 [from: ...]` line into a trusted `fact` as long as some
    receipt somewhere in the window happened to say the same words.
    """
    out = {}
    for ln in window.lines(trusted=True):
        key = (ln.kind, ln.stripped)
        out[key] = out.get(key, 0) + 1
    return out


def main():
    root = os.environ.get("SUPERJEV_BENCH_ROOT", DEFAULT_ROOT)
    found = list(cases(root))
    if not found:
        print(f"replay_window_model: FAIL — no recorded benches under {root} "
              f"(set SUPERJEV_BENCH_ROOT); an empty replay cannot establish "
              f"a passing result")
        return 1

    print(f"composer under test: {COMPOSER_DIR}")

    byte_ok, byte_bad = [], []
    meta_ok, meta_bad = [], []
    trip_ok, trip_lossy = [], []
    cut_diff = []
    unsafe = []
    empty = []
    tail_cut = []

    for cid, path, _draft in found:
        records = sj._read_transcript_records(path)
        want, want_meta = sj._derive_evidence_text_from_transcript(
            path, session_id=None, return_meta=True)
        win = wm.from_transcript(path, session_id=None, records=records)
        got = win.render()
        tr = win.truncated
        was_cut = bool(tr.legacy_tail_cut or tr.prev_turn_truncated
                       or tr.reports_bytes_cut)

        if (want or "") == (got if got.strip() else ""):
            byte_ok.append(cid)
        elif want is None and not got.strip():
            byte_ok.append(cid)
            empty.append(cid)
        elif was_cut and not OWN_COMPOSER:
            cut_diff.append(cid)
        else:
            byte_bad.append((cid, want, got))

        got_meta = dict(win.meta)
        if got_meta == want_meta:
            meta_ok.append(cid)
        elif was_cut and not OWN_COMPOSER:
            if cid not in cut_diff:
                cut_diff.append(cid)
        else:
            diff = {k: (want_meta.get(k), got_meta.get(k))
                    for k in set(want_meta) | set(got_meta)
                    if want_meta.get(k) != got_meta.get(k)}
            meta_bad.append((cid, diff))

        reparsed = wm.from_text(got, byte_cap=win.byte_cap)
        if win.truncated.legacy_tail_cut:
            tail_cut.append(cid)
        elif reparsed == win:
            trip_ok.append(cid)
        else:
            trip_lossy.append(cid)
        # Property 4: re-parsing bytes may LOSE trust, never invent it.
        if win.truncated.legacy_tail_cut:
            # The tail cut lands mid-line, so whole-line identity says
            # nothing; containment in trusted TEXT is the same guarantee
            # at the only granularity the bytes still support. The kind
            # cannot be checked here for the same reason.
            haystack = "\n".join(p.text for p in win.pieces if p.trusted)
            for _kind, text in _trusted_lines(reparsed):
                if text and text not in haystack:
                    unsafe.append((cid, text))
                    break
        else:
            model_trusted = _trusted_lines(win)
            for key, count in _trusted_lines(reparsed).items():
                if model_trusted.get(key, 0) < count:
                    unsafe.append((cid, key))
                    break
                model_trusted[key] -= count

    n = len(found)
    print(f"cases replayed: {n}")
    print(f"byte-identical to the composer: {len(byte_ok)}/{n}")
    print(f"meta-identical to the composer: {len(meta_ok)}/{n}")
    if cut_diff:
        print(f"windows a byte truncation cut into, where this composer "
              f"repairs a sliced fence and the model does not: "
              f"{len(cut_diff)} ({', '.join(cut_diff)})")
    print(f"from_text round-trips to the same pieces: "
          f"{len(trip_ok)}/{n - len(tail_cut)} (excluding "
          f"{len(tail_cut)} legacy tail-cut windows)")
    if empty:
        print(f"windows the composer returns None for: {len(empty)}")
    for cid, diff in meta_bad:
        print(f"  META DIFF {cid}: {diff}")
    for cid, want, got in byte_bad[:3]:
        print(f"  BYTE DIFF {cid}: want {len((want or '').encode())}B "
              f"got {len(got.encode())}B")
        w, g = (want or "").splitlines(), got.splitlines()
        for i in range(max(len(w), len(g))):
            a = w[i] if i < len(w) else "<none>"
            b = g[i] if i < len(g) else "<none>"
            if a != b:
                print(f"    line {i + 1}: composer {a[:120]!r}")
                print(f"    line {i + 1}: model    {b[:120]!r}")
                break
    print(f"no line gained trust or changed piece kind on re-parse: "
          f"{n - len(unsafe)}/{n}")
    for cid, text in unsafe[:5]:
        print(f"  UNSAFE {cid}: from_text trusts a line from_transcript "
              f"does not, or trusts it as a different kind: {text!r}")
    if trip_lossy:
        print("  round-trip lossy (safe direction, see docs/window-model.md): "
              + ", ".join(trip_lossy))
    rc = 0 if not byte_bad and not meta_bad and not unsafe else 1
    return rc if _run_pr53_child() == 0 else 1


def _run_pr53_child():
    """Run this same replay again in a child process, against PR #53's
    composer, and return its exit code. 0 when there is nothing to run.

    A child rather than a second pass in this process: `window_model`
    imports `superjev` once, lazily, and caches it in `sys.modules`, so
    there is exactly one composer per interpreter and swapping it
    mid-flight would compare the model against a half-loaded module.

    It does NOT skip when PR #53's worktree is present. That was the whole
    point of the byte-identity claim: `_render_report` and
    `_reports_region_mark` both feature-detect the composer, and a claim
    that they match a branch nobody ever ran them against is a claim about
    nothing.
    """
    if os.environ.get("SUPERJEV_WM_COMPOSER_DIR"):
        return 0            # already the child
    composer = PR53_DIR / "skills/super-jev"
    if not (composer / "superjev.py").is_file():
        print(f"PR #53 composer not on this machine ({composer}) — "
              f"byte-identity against it NOT checked")
        return 0
    print()
    print(f"=== re-running against PR #53's composer at {composer}")
    env = dict(os.environ, SUPERJEV_WM_COMPOSER_DIR=str(composer))
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                          env=env, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        print(f"  | {line}")
    for line in (proc.stderr or "").splitlines()[-10:]:
        print(f"  ! {line}")
    print(f"=== PR #53 composer run exit {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
