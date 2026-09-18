#!/usr/bin/env python3
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
     just on bytes.
  4. The SAFETY direction, on every case including the ones property 3
     does not hold for: every line `from_text` marks trusted is a line
     `from_transcript` also marks trusted. Re-parsing composed bytes may
     lose trust; it must never invent it. On a window that took the
     composer's last-resort whole-window byte tail-keep, whole-line
     identity is meaningless — that cut lands mid-line — so there the
     check is that every trusted re-parsed line is CONTAINED in text
     `from_transcript` trusts, which is the same guarantee at the only
     granularity the bytes still support.

Property 3 is the one that can legitimately fail on recorded bytes, and
the number that fails is a measurement worth having rather than a bug:
`from_text` infers provenance from composed text, so a tool result whose
own output contains an item separator, or which prints a `REPORT FROM`
line, re-parses into a different set of pieces. Property 4 is what makes
that acceptable, and properties 1 and 4 cover every case regardless.

    python3 skills/super-jev/tests/replay_window_model.py

Exits 0 iff every case is byte-identical and meta-identical. Prints the
round-trip tally. Exits 0 with a skip note when no bench is on this
machine (the benches live outside the repo, under
~/super-jev-experiments).
"""
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
import superjev as sj            # noqa: E402
import window_model as wm        # noqa: E402

DEFAULT_ROOT = "/Users/admin/super-jev-experiments"
#: The three recorded gate benches, 40 + 29 + 30 = 99 cases.
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
    """{stripped line text: count} over every TRUSTED content line."""
    out = {}
    for ln in window.lines(trusted=True):
        out[ln.stripped] = out.get(ln.stripped, 0) + 1
    return out


def main():
    root = os.environ.get("SUPERJEV_BENCH_ROOT", DEFAULT_ROOT)
    found = list(cases(root))
    if not found:
        print(f"replay_window_model: no recorded benches under {root} "
              f"(set SUPERJEV_BENCH_ROOT) — nothing to replay, skipping")
        return 0

    byte_ok, byte_bad = [], []
    meta_ok, meta_bad = [], []
    trip_ok, trip_lossy = [], []
    unsafe = []
    empty = []
    tail_cut = []

    for cid, path, _draft in found:
        records = sj._read_transcript_records(path)
        want, want_meta = sj._derive_evidence_text_from_transcript(
            path, session_id=None, return_meta=True)
        win = wm.from_transcript(path, session_id=None, records=records)
        got = win.render()
        if (want or "") == (got if got.strip() else ""):
            byte_ok.append(cid)
        elif want is None and not got.strip():
            byte_ok.append(cid)
            empty.append(cid)
        else:
            byte_bad.append((cid, want, got))

        got_meta = dict(win.meta)
        if got_meta == want_meta:
            meta_ok.append(cid)
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
            haystack = "\n".join(p.text for p in win.pieces if p.trusted)
            for text in _trusted_lines(reparsed):
                if text and text not in haystack:
                    unsafe.append((cid, text))
                    break
        else:
            model_trusted = _trusted_lines(win)
            for text in _trusted_lines(reparsed):
                if model_trusted.get(text, 0) <= 0:
                    unsafe.append((cid, text))
                    break
                model_trusted[text] -= 1

    n = len(found)
    print(f"cases replayed: {n}")
    print(f"byte-identical to the composer: {len(byte_ok)}/{n}")
    print(f"meta-identical to the composer: {len(meta_ok)}/{n}")
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
    print(f"no line gained trust on re-parse: "
          f"{n - len(unsafe)}/{n}")
    for cid, text in unsafe[:5]:
        print(f"  UNSAFE {cid}: from_text trusts a line from_transcript "
              f"does not: {text[:100]!r}")
    if trip_lossy:
        print("  round-trip lossy (safe direction, see docs/window-model.md): "
              + ", ".join(trip_lossy))
    return 0 if not byte_bad and not meta_bad and not unsafe else 1


if __name__ == "__main__":
    sys.exit(main())
