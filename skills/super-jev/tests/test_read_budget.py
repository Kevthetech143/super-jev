#!/usr/bin/env python3
"""Offline tests for how much of one note a single content check reads (context setting 3/7).

The guards here were committed FIRST, on the unchanged code, with the fixture they compare against:

  test_small_reads_are_byte_identical_to_main   at the preset small (the default), every payload the content check
      would send, for a corpus of notes from 1 KB to 200 KB (a zoom-chosen-parts file, a code file with an outline, a
      table-heavy note, a non-ASCII note, a claim check, a secret blanked, a file held), equals the bytes main sent.
  test_word_search_ranking_and_index_stamp_do_not_move   the free stage and the word index are not touched.

The fixture, fixtures/context/small_payloads.json, was generated on origin/main 576f16c (Release 1.0.138) by

    python3 skills/super-jev/tests/test_read_budget.py --write-golden

Never regenerate it to make a failure go away: small must read what main read. The notes are built from fixed
arithmetic, never random, so they are the same bytes on every machine.

No network, no key, no Jev call.

    python3 -m pytest skills/super-jev/tests/test_read_budget.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "context" / "small_payloads.json"
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_read_budget", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)

# ---------------------------------------------------------------- the notes
_WORDS = ("amber anchor basil beacon cedar copper delta ember fable garnet harbor indigo jasper kettle lantern "
          "meadow nickel orchid pebble quartz ripple saddle timber umber velvet willow yonder zephyr "
          "bramble cobalt dune falcon glacier hollow island juniper kelp lagoon marble nectar olive prairie").split()
ANSWER = "The zeppelin quokka inspection is scheduled for the ninth Thursday of each quarter.\n"
QUESTION = "when is the zeppelin quokka inspection scheduled"
COMMON = "which kettle lantern entries mention the lagoon"  # words every filler line uses: no passage stands out
CLAIM = "The zeppelin quokka inspection is scheduled for the ninth Thursday of each quarter"
# A secret-shaped line, built from pieces so that no file in the repo holds one whole.
SECRET_LINE = "pass" + "word: " + "hunter" + "2"


def filler(i):
    """Line i of a plain note: words drawn by arithmetic from a fixed list, never by chance."""
    n = len(_WORDS)
    return (f"- {_WORDS[i * 7 % n]} {_WORDS[(i * 11 + 3) % n]} {_WORDS[(i * 13 + 5) % n]} entry {i}: "
            f"{_WORDS[(i * 17 + 1) % n]} {_WORDS[(i * 19 + 2) % n]} {_WORDS[(i * 23 + 4) % n]} noted again.\n")


def note(chars, answer_at=None, title="Harbor log", per_section=30):
    """A markdown note of exactly `chars` characters: a title, a heading every `per_section` lines (so a secret-shaped
    line withholds one section and not the file), filler lines, and ANSWER in place of the filler line nearest
    `answer_at` (0 to 1 of the way through; none when None)."""
    out, total, i = [f"# {title}\n"], len(title) + 3, 0
    while total < chars + 200:
        if i % per_section == 0:
            out.append(f"\n## Section {i // per_section}\n")
            total += len(out[-1])
        out.append(filler(i))
        total += len(out[-1])
        i += 1
    text = "".join(out)
    if answer_at is not None:
        at = text.rfind("\n- ", 0, int(len(text) * answer_at)) + 1
        text = text[:at] + ANSWER + text[text.index("\n", at) + 1:]
    return text[:chars]


def table_note(chars, answer_at=None):
    """A note that is mostly pipe tables (a heading and a header row every 25 rows)."""
    n = len(_WORDS)
    out, total, i = ["# Harbor tables\n"], 16, 0
    while total < chars + 200:
        if i % 25 == 0:
            out.append(f"\n## Table {i // 25}\n\n| Item | Zone | Count | Notes |\n| --- | --- | --- | --- |\n")
            total += len(out[-1])
        out.append(f"| {_WORDS[i * 7 % n]} {i} | {_WORDS[(i * 11 + 3) % n]} | {i % 97} | {_WORDS[(i * 13 + 5) % n]} noted |\n")
        total += len(out[-1])
        i += 1
    text = "".join(out)
    if answer_at is not None:
        at = text.rfind("\n| ", 0, int(len(text) * answer_at)) + 1
        text = text[:at] + "| zeppelin quokka | inspection | 9 | scheduled ninth Thursday |\n" + text[text.index("\n", at) + 1:]
    return text[:chars]


_ES = "café ñandú piñata almacén revisión número jardín corazón cañón sábado miércoles".split()


def spanish_note(chars, answer_at=None):
    """Accented Spanish with a little Japanese in every line: more than one byte to the character."""
    n = len(_ES)
    out, total, i = ["# Registro del puerto\n"], 22, 0
    while total < chars + 200:
        if i % 30 == 0:
            out.append(f"\n## Sección {i // 30}\n")
            total += len(out[-1])
        out.append(f"- {_ES[i * 7 % n]} {_ES[(i * 11 + 3) % n]} {_ES[(i * 13 + 5) % n]} número {i}: "
                    f"{_ES[(i * 17 + 1) % n]} del almacén, 検査記録 {i}。\n")
        total += len(out[-1])
        i += 1
    text = "".join(out)
    if answer_at is not None:
        at = text.rfind("\n- ", 0, int(len(text) * answer_at)) + 1
        text = (text[:at] + "La inspección del zepelín quokka está programada para el noveno jueves de cada trimestre.\n"
                + text[text.index("\n", at) + 1:])
    return text[:chars]


def code_note(chars):
    """A Python module of small functions; one docstring holds ANSWER."""
    out, total, i = ['"""Harbor checks."""\n'], 20, 0
    while total < chars + 200:
        doc = ANSWER.strip() if i == 40 else f"Check zone {i}: {_WORDS[i * 7 % len(_WORDS)]} {_WORDS[(i * 11 + 3) % len(_WORDS)]}."
        out.append(f'\n\ndef check_{i}(zone, count):\n    """{doc}"""\n    return zone * count + {i}\n')
        total += len(out[-1])
        i += 1
    return "".join(out)[:chars]


OUTLINE = ("Outline of harbor.py (facts from connected files, each with its path:line)\n"
           "purpose (harbor.py:1): Harbor checks.\n"
           "part check_40 (harbor.py:211-214): The zeppelin quokka inspection is scheduled for the ninth Thursday of each quarter.\n"
           "check_40 is called at ops.py:12")


def with_secret(text, at):
    """`text` with SECRET_LINE in place of the filler line nearest character `at`."""
    pos = text.rfind("\n- ", 0, at) + 1
    return text[:pos] + SECRET_LINE + text[text.index("\n", pos):]


def held_note():
    """A note that is mostly secret-shaped lines: it cannot be made clean, so it is held whole."""
    return "# Logins\n" + "".join("pass" + f"word: hunter{i}\n" for i in range(60)) + "plain line\n" * 20


def cases():
    """The notes the golden payloads are built from: id -> {name, text, question, and optionally the zoom's chosen
    `parts`, a code file's `outline`, a `claim`}. Sizes run from 1 KB to 200 KB, on both sides of the 12,000-character
    line below which a note is one passage and of the 14,000 a check reads."""
    out = {}

    def add(cid, name, text, question=QUESTION, **extra):
        out[cid] = {"name": name, "text": text, "question": question, **extra}

    add("tiny_1kb", "tiny.md", note(1_000, 0.5))
    add("one_passage_11999", "whole.md", note(11_999, 0.5))
    add("split_all_fit_12100", "split.md", note(12_100, 0.5))
    add("split_all_fit_13600", "edge.md", note(13_600, 0.5))
    add("split_cut_13900", "over.md", note(13_900, 0.5))
    add("cut_20kb_answer_last", "twenty.md", note(20_000, 0.9))
    add("cut_40kb_answer_middle", "forty.md", note(40_000, 0.5))
    add("cut_80kb_answer_early", "eighty.md", note(80_000, 0.1))
    add("cut_80kb_common_words", "common.md", note(80_000, 0.5), COMMON)
    add("cut_150kb_no_answer", "big.md", note(150_000))
    add("cut_200kb_answer_last", "huge.md", note(200_000, 0.95))
    big = note(30_000, 0.5)
    add("zoom_parts_one_long", "parts.md", big, parts=[["Overview", 1, 14], ["Quarterly inspections", 61, 300],
                                                       ["Closing notes", 301, 330]])
    add("code_file_with_outline", "harbor.py", code_note(30_000), outline=OUTLINE)
    add("table_heavy_30kb", "tables.md", table_note(30_000, 0.6))
    add("non_ascii_25kb", "puerto.md", spanish_note(25_000, 0.7), "cuándo es la inspección del zepelín quokka programada")
    add("claim_check_40kb", "claim.md", note(40_000, 0.5), CLAIM, claim=CLAIM)
    add("secret_blanked_40kb", "blanked.md", with_secret(note(40_000, 0.9), 22_000))
    add("secret_holds_the_file", "held.md", held_note())
    return out


def start(tmp_path, case):
    """confirm_start's answer for one note: (a finished result, None) or (None, what confirm_finish needs). The zoom's
    chosen parts and a code file's outline are staged the way an ask stages them, a claim the way --claim sets it."""
    f = tmp_path / case["name"]
    f.write_text(case["text"], encoding="utf-8")
    stage = {}
    if case.get("parts"):
        stage["toc_parts"] = {str(f): case["parts"]}
    if case.get("outline"):
        stage["outlines"] = {str(f): {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "text": case["outline"]}}
    ask._STAGE = stage
    ask._CLAIM["text"] = case.get("claim")
    return ask.confirm_start(case["question"], str(f))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize(done, ctx):
    """A payload down to its bytes: which passages were read, each leaf's size and hash, and the hash of the exact
    text that goes to navigation-cli."""
    if ctx is None:
        return {"finished": list(done)}
    payload = ctx["payload"]
    sent = json.dumps(payload)
    return {"picked": ctx["picked"],
            "leaves": [[n["id"], n["label"], len(n["description"]), _sha(n["description"])]
                       for n in payload["catalog"]["nodes"][1:]],
            "bytes": len(sent.encode("utf-8")), "sha256": _sha(sent)}


#: Where the word-search notes live. A file's whole path counts in the word search (three times in every passage), so a
#: temporary folder would move the scores from one run to the next: these files exist only in memory, at a fixed place.
VIRTUAL = "/notes/harbor"


def word_search_notes():
    return {"a_tiny.md": note(1_000, 0.5), "b_over.md": note(13_900, 0.5), "c_forty.md": note(40_000, 0.5),
            "d_eighty.md": note(80_000, 0.1)}


def ranking(mp, question):
    """What the word search (the free stage, step 4) ranks for a question over word_search_notes(): [[name, score], ...]."""
    cache, reads = {}, {}
    for name, text in word_search_notes().items():
        raw = text.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        cache[f"{VIRTUAL}/{name}"] = {"sha256": sha, "pass": True, "description": "", "question": ""}
        reads[f"{VIRTUAL}/{name}"] = (raw, sha)
    mp.setattr(ask, "load_cache_files", lambda ptr: cache)
    return [[Path(path).name, round(score, 6)] for score, path, _ptr in ask.word_search(question, ["p1"], reads=reads)]


def golden_data(tmp_path, mp):
    """Everything the fixture holds, computed from the code as it is now."""
    mp.setattr(ask, "_STAGE", {})
    mp.setitem(ask._CLAIM, "text", None)
    payloads = {cid: summarize(*start(tmp_path, case)) for cid, case in cases().items()}
    return {
        "_comment": "Generated on origin/main 576f16c by test_read_budget.py --write-golden, on the unchanged code. "
                    "Small must read what main read: never regenerate this to make a failure go away.",
        "payloads": payloads,
        "word_search": {q: ranking(mp, q) for q in (QUESTION, COMMON)},
        "index": {"stamp": ask.WORD_INDEX_VERSION,
                  "item": _sha(json.dumps(ask._index_item(note(40_000, 0.5), "sha"), sort_keys=True))},
    }


def dump(data):
    """The fixture as text, one line per case, so that a change shows which case moved."""
    parts = []
    for key, value in data.items():
        if key in ("payloads", "word_search"):
            rows = ",\n".join(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in value.items())
            parts.append(f" {json.dumps(key)}: {{\n{rows}\n }}")
        else:
            parts.append(f" {json.dumps(key)}: {json.dumps(value)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


# ---------------------------------------------------------------- guards: green before the change and after it
@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Each test starts with no staged zoom picks and no claim; both are put back afterwards."""
    monkeypatch.setattr(ask, "_STAGE", {})
    monkeypatch.setitem(ask._CLAIM, "text", None)


def test_small_reads_are_byte_identical_to_main(tmp_path):
    want = json.loads(GOLDEN.read_text())["payloads"]
    got = {cid: summarize(*start(tmp_path, case)) for cid, case in cases().items()}
    assert sorted(got) == sorted(want)
    differ = {cid: sorted(k for k in set(got[cid]) | set(want[cid]) if got[cid].get(k) != want[cid].get(k))
              for cid in got if got[cid] != want[cid]}
    assert not differ, f"small no longer sends what main sent (case: fields that differ): {differ}"
    # the corpus does reach the read rule: some notes are read whole, some are cut to the passages that match
    cut = [cid for cid, s in got.items() if "picked" in s and s["picked"] != list(range(len(s["leaves"])))]
    whole = [cid for cid, s in got.items() if "picked" in s and s["picked"] == list(range(len(s["leaves"])))]
    assert len(cut) >= 8 and len(whole) >= 4, (cut, whole)


def test_word_search_ranking_and_index_stamp_do_not_move(monkeypatch):
    # what must not vary by preset: the passage size, the whole-note size, the title prefix, how many files one ask reads
    assert (ask.CONFIRM_CHUNK, ask.WHOLE_FILE_CHARS, ask.SUBJECT_CHARS, ask.SUBJECT_SEP) == (3500, 12000, 100, "\n...\n")
    assert (ask.READ_MAX, ask.WORD_HITS, ask.WORDS_VERSION) == (10, 5, 4)
    want = json.loads(GOLDEN.read_text())
    assert ask.WORD_INDEX_VERSION == want["index"]["stamp"]
    assert _sha(json.dumps(ask._index_item(note(40_000, 0.5), "sha"), sort_keys=True)) == want["index"]["item"]
    for question, expected in want["word_search"].items():
        assert ranking(monkeypatch, question) == expected, question


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-golden"]:
        sys.exit(__doc__)
    import tempfile
    with tempfile.TemporaryDirectory() as d, pytest.MonkeyPatch.context() as mp:
        data = golden_data(Path(d), mp)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(dump(data))
    print(f"wrote {GOLDEN} ({len(data['payloads'])} payloads)")
