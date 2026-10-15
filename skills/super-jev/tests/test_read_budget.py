#!/usr/bin/env python3
"""Offline tests for how much of one note a single content check reads (context setting 3/7).

The one packing rule is pick_chunks: a note's passages are read whole when they fit the budget, else the ones that
best match the question's words, best first, until the budget is spent. The budget is read_budget(): 14,000 characters
at the preset small (what it always was), 56,000 at medium and 112,000 at large (judge_profiles.json, the same share of
each window), and always 14,000 for a claim check and for a file re-sent after a fallback.

Two guards were committed FIRST, on the unchanged code, with the fixture they compare against, and stay green:

  test_small_reads_are_byte_identical_to_main   at the preset small (the default), every payload the content check
      would send, for a corpus of notes from 1 KB to 200 KB (a zoom-chosen-parts file, a code file with an outline, a
      table-heavy note, a non-ASCII note, a claim check, a secret blanked, a file held), equals the bytes main sent.
  test_word_search_ranking_and_index_stamp_do_not_move   the free stage and the word index are not touched.

The fixture, fixtures/context/small_payloads.json, was generated on origin/main 576f16c (Release 1.0.138) by

    python3 skills/super-jev/tests/test_read_budget.py --write-golden

Never regenerate it to make a failure go away: small must read what main read. The notes are built from fixed
arithmetic, never random, so they are the same bytes on every machine.

The other tests are new and, but for one, fail on the base, which has one fixed budget. They set the preset through the
module's own state, _CONTEXT, which is what an ask sets once from the flag, the environment or the saved choice (a later
item). The exception, test_ask_loads_beside_an_older_build_whose_profile_has_no_read_chars, guards a failure of the change
itself (found by the scorecard) and so passes on the base.

The shipped table offers Small and Medium only (Large waits for its own test). The tests that read at large, and the ones that
walk every preset, read a made-up table: the shipped one plus a `large` row (the large_row fixture in conftest.py).

No network, no key, no Jev call: a fake navigation stands in for the content check, and the one test that runs the real
navigation-cli does it against an offline fetch hook.

    python3 -m pytest skills/super-jev/tests/test_read_budget.py -q
"""
import dataclasses
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[1]
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "context" / "small_payloads.json"
TABLE_HOOK = REPO / "test" / "fixtures" / "judge-table-hook.cjs"  # hands a child process the made-up table (SJ_TABLE, set by large_row)
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_read_budget", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
judge_profile = ask.judge_profile

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


def put_answers(text, fractions):
    """`text` with ANSWER in place of the filler line nearest each fraction (0 to 1) of the way through it."""
    for fraction in fractions:
        at = text.rfind("\n- ", 0, int(len(text) * fraction)) + 1
        text = text[:at] + ANSWER + text[text.index("\n", at) + 1:]
    return text


def japanese_note(chars):
    """Three bytes to the character, the heaviest text a note is likely to hold."""
    out, total, i = ["# 港の記録\n"], 7, 0
    while total < chars + 200:
        if i % 30 == 0:
            out.append(f"\n## 第{i // 30}章\n")
            total += len(out[-1])
        out.append(f"- 灯台の点検記録{i}：港の北側の桟橋で第九木曜日に実施される予定である。\n")
        total += len(out[-1])
        i += 1
    return "".join(out)[:chars]


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


# ---------------------------------------------------------------- the budget: these fail on the base
PRESETS = {"small": 14_000, "medium": 56_000, "large": 112_000}  # characters of one note a check reads, fixed before any run
TITLE = ask.SUBJECT_CHARS + len(ask.SUBJECT_SEP)  # room pick_chunks keeps on every passage after the first for the title


def at(monkeypatch, preset):
    """Read as `preset` for this test. raising=False: the base keeps no such state and reads 14,000 whatever it is told,
    so a test of what is read runs there and fails on that."""
    monkeypatch.setattr(ask, "_CONTEXT", {"name": preset, "small_paths": set()}, raising=False)


def sent(ctx):
    """The text of every passage one payload sends."""
    return [n["description"] for n in ctx["payload"]["catalog"]["nodes"][1:]]


def sent_chars(ctx):
    return sum(map(len, sent(ctx)))


def cost(chunks, picked):
    """What pick_chunks charges for the passages `picked`: their lengths, and the title's room on each after the first."""
    return sum(len(chunks[i]) + (TITLE if i else 0) for i in picked)


def filled(chunks, picked, budget):
    """The read is within the budget and leaves less than one more passage unspent: what a best-first fill ends on."""
    return 0 <= budget - cost(chunks, picked) < max(map(len, chunks)) + TITLE


@pytest.mark.usefixtures("large_row")
def test_read_budget_is_14000_at_small_and_comes_from_the_profile(monkeypatch):
    prof = judge_profile.PROFILE
    assert prof.read_chars == ask.READ_CHARS == 14_000
    assert ask.read_budget() == 14_000 and ask.read_budget("any/note.md") == 14_000  # small is the preset when none is chosen
    for preset, chars in PRESETS.items():  # the others come from the same table: nothing in ask.py states a size
        at(monkeypatch, preset)
        assert ask.read_budget() == chars == judge_profile.load(context=preset).read_chars, preset
    # a profile that states another size changes the budget: it is read from the profile, not written in ask.py
    monkeypatch.setattr(judge_profile, "PROFILE", dataclasses.replace(prof, read_chars=9_000))
    again = importlib.util.spec_from_file_location("ask_read_budget_9000", SKILL / "ask.py")
    other = importlib.util.module_from_spec(again)
    again.loader.exec_module(other)
    assert other.READ_CHARS == other.read_budget() == 9_000
    # the factor the old size was built from is gone (named in two pieces: the proof command greps the repo for it)
    assert not hasattr(ask, "CONFIRM_CHUNKS" + "_PER_FILE")


def test_ask_loads_beside_an_older_build_whose_profile_has_no_read_chars(monkeypatch):
    """scorecard.py grades two builds in one process: before it loads the second it drops the first one's sibling files,
    but not its `judges` package, so the second is handed a profile that may be an older build's."""
    class Older:
        def __getattr__(self, name):
            if name == "read_chars":
                raise AttributeError(name)
            return getattr(judge_profile.PROFILE, name)
    monkeypatch.setattr(ask.judges, "profile", lambda: Older())
    beside = importlib.util.spec_from_file_location("ask_read_budget_beside", SKILL / "ask.py")
    other = importlib.util.module_from_spec(beside)
    beside.loader.exec_module(other)
    assert other.READ_CHARS == 14_000


def test_medium_reads_every_passage_of_a_40kb_note(tmp_path, monkeypatch):
    text = note(40_000, 0.5)
    passages = len(ask.split_passages(text))
    assert passages == 12
    case = {"name": "forty.md", "text": text, "question": QUESTION}
    at(monkeypatch, "small")
    assert len(start(tmp_path, case)[1]["picked"]) <= 4  # control: small keeps the few passages that match, of the twelve
    at(monkeypatch, "medium")
    done, ctx = start(tmp_path, case)
    assert done is None and ctx["picked"] == list(range(passages))
    assert sent_chars(ctx) >= len(text)  # the whole note goes out, each passage after the first led by its title


def test_medium_fills_56000_with_the_best_passages_of_a_150kb_note(tmp_path, monkeypatch):
    # six passages hold the question's words (the answer, in six places); the rest of the 150 KB hold none of them
    text = put_answers(note(150_000), (0.15, 0.30, 0.45, 0.60, 0.75, 0.90))
    chunks = ask.split_passages(text)
    answers = [i for i, c in enumerate(chunks) if "zeppelin" in c]
    assert len(chunks) >= 40 and len(answers) == 6
    at(monkeypatch, "medium")
    done, ctx = start(tmp_path, {"name": "big.md", "text": text, "question": QUESTION})
    picked = ctx["picked"]
    assert filled(chunks, picked, PRESETS["medium"]), (cost(chunks, picked), len(picked))  # 56,000 spent, as near as passages allow
    assert set(answers) <= set(picked)  # best first: every passage that matches is read
    rest = [i for i in picked if i not in answers]
    assert rest == [i for i in range(len(chunks)) if i not in answers][:len(rest)]  # then on from the top of the note
    assert picked == sorted(picked) and len(picked) < len(chunks)


@pytest.mark.usefixtures("large_row")
def test_large_reads_a_100kb_note_whole_and_cuts_a_250kb_note_to_112000(tmp_path, monkeypatch):
    at(monkeypatch, "large")
    text = note(100_000, 0.5)  # 29 passages and a title on 28 of them: 102,940 characters or fewer, under 112,000
    passages = len(ask.split_passages(text))
    done, ctx = start(tmp_path, {"name": "hundred.md", "text": text, "question": QUESTION})
    assert done is None and ctx["picked"] == list(range(passages)) and passages >= 28
    at(monkeypatch, "medium")  # control: medium cuts the same note
    assert len(start(tmp_path, {"name": "hundred.md", "text": text, "question": QUESTION})[1]["picked"]) < passages
    ceiling = judge_profile.load().file_ceiling_bytes  # the largest note that connects whole
    text = note(ceiling, 0.5)
    chunks = ask.split_passages(text)
    at(monkeypatch, "large")
    done, ctx = start(tmp_path, {"name": "ceiling.md", "text": text, "question": QUESTION})
    assert filled(chunks, ctx["picked"], PRESETS["large"]) and len(ctx["picked"]) < len(chunks)
    assert any("zeppelin" in chunks[i] for i in ctx["picked"])  # the passage that answers is among those read


def test_ties_go_to_the_earlier_passage_and_a_zero_score_one_that_does_not_fit_ends_the_fill():
    # eleven passages of 1,000 characters that match the question alike: read from the top until the budget is spent
    tied = [("zeppelin quokka " + "x" * 984)[:1_000] for _ in range(11)]
    for budget in (5_000, 7_000, 12_000):
        fit = 1 + (budget - 1_000) // (1_000 + TITLE)
        assert ask.pick_chunks(QUESTION, tied, budget) == list(range(min(fit, 11))), budget
    plain = lambda n: "x" * n  # none of the question's words
    match = lambda times, n: (("zeppelin quokka " * times) + "x" * n)[:n]
    chunks = [plain(3_000), match(60, 8_000), match(5, 4_000), plain(500), match(1, 1_000)]
    #   cost:   3,000          8,105 best        4,105             605 (none)    1,105 (weakest match)
    # Best first: passage 1 is read (8,105). Passage 2 matches but does not fit, so it is passed over and the fill goes
    # on: passage 4 fits (9,210). Then the passages that match nothing, from the top: passage 0 does not fit and matches
    # nothing, so the fill ends there and passage 3, which would have fitted, is not read (never skip ahead).
    assert ask.pick_chunks(QUESTION, chunks, 10_000) == [1, 4]
    assert ask.pick_chunks(QUESTION, chunks, cost(chunks, range(5))) == [0, 1, 2, 3, 4]  # all of them fit: whole
    assert ask.pick_chunks(QUESTION, chunks, cost(chunks, range(5)) - 1) != [0, 1, 2, 3, 4]  # one character short: cut


@pytest.mark.usefixtures("large_row")
def test_claim_checks_read_at_small_whatever_the_preset(tmp_path, monkeypatch):
    text = note(150_000, 0.5)
    chunks = ask.split_passages(text)
    question = {"name": "claimed.md", "text": text, "question": QUESTION}
    claim = {**question, "question": CLAIM, "claim": CLAIM}
    at(monkeypatch, "small")
    claimed_at_small = json.dumps(start(tmp_path, claim)[1]["payload"])
    for preset, chars in reversed(PRESETS.items()):
        at(monkeypatch, preset)
        _, asked = start(tmp_path, question)  # an ordinary question reads the preset's size
        assert filled(chunks, asked["picked"], chars), preset
        assert ask.read_budget() == chars, preset
        _, claimed = start(tmp_path, claim)  # a claim check always reads small's: its shape was tuned for a few passages
        assert ask.read_budget() == ask.READ_CHARS == 14_000, preset
        assert json.dumps(claimed["payload"]) == claimed_at_small, preset


@pytest.mark.usefixtures("large_row")
def test_a_file_re_sent_after_a_fallback_is_read_at_small(tmp_path, monkeypatch):
    case = {"name": "resent.md", "text": note(150_000, 0.5), "question": QUESTION}
    at(monkeypatch, "large")
    _, whole = start(tmp_path, case)
    assert sent_chars(whole) > 14_000
    ask._CONTEXT["small_paths"].add(str(tmp_path / "resent.md"))  # what a fallback records for the file it re-sends
    _, again = start(tmp_path, case)
    assert sent_chars(again) <= 14_000
    assert ask.read_budget(str(tmp_path / "resent.md")) == 14_000 and ask.read_budget(str(tmp_path / "other.md")) == 112_000


@pytest.mark.usefixtures("large_row")
def test_a_secret_past_character_14000_is_blanked_or_holds_the_file_at_every_preset(tmp_path, monkeypatch):
    text = with_secret(note(40_000, 0.9), 22_000)
    assert text.index(SECRET_LINE) > 14_000  # small never read this far; medium and large do
    blanked = {"name": "past.md", "text": text, "question": QUESTION}
    held = {"name": "logins.md", "text": held_note(), "question": QUESTION}
    for preset in PRESETS:
        at(monkeypatch, preset)
        done, ctx = start(tmp_path, blanked)
        assert done is None, preset
        out = "\n".join(sent(ctx))
        assert "hunter" not in out and SECRET_LINE not in out and not ask.payload_has_secret(ctx["payload"]), preset
        if preset != "small":  # the section is read, as a marker where the line was
            assert ask.toc_search.WITHHELD in out and sent_chars(ctx) > ask.READ_CHARS, preset
        assert start(tmp_path, held) == ((None, False, None, ask.HELD_SECRET), None), preset  # a file that cannot be made clean never goes


def ask_over(tmp_path, monkeypatch, connected):
    """One ask over `connected` notes: twelve of 20,000 characters that hold the answer (each over what small reads) and
    the rest tiny. The zoom's own picks are the next five, so ten files are on the read list; navigation is faked.
    Returns (the names of the files opened, the characters sent for each)."""
    folder = tmp_path / f"set{connected}"
    folder.mkdir()
    notes = {f"big{i:02d}.md": note(20_000, 0.5, title=f"Harbor log {i}") for i in range(12)}
    notes.update({f"tiny{i:03d}.md": f"# Note {i}\n{_WORDS[i % len(_WORDS)]} quokka {i}\n" for i in range(connected - 12)})
    cache = {}
    for name, text in notes.items():
        (folder / name).write_text(text)
        cache[str(folder / name)] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "pass": True, "description": "", "question": ""}
    routed = [{"score": 0.9 - n / 100, "originalPath": str(folder / f"big{n:02d}.md")} for n in range(5, 10)]

    def memory(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "candidates", "candidates": routed}

    opened, payloads, real_start = [], [], ask.confirm_start
    monkeypatch.setattr(ask, "load_cache_files", lambda ptr: cache)
    monkeypatch.setattr(ask, "memory", memory)
    monkeypatch.setattr(ask, "confirm_start", lambda q, path: opened.append(Path(path).name) or real_start(q, path))
    monkeypatch.setattr(ask, "run_navigation",
                        lambda payload: payloads.append(payload) or ({"status": "no-candidates", "candidates": []}, None))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / f"state{connected}"))
    ask.lookup(QUESTION, "me", tmp_path / f"s{connected}")
    return opened, [sum(len(n["description"]) for n in pl["catalog"]["nodes"][1:]) for pl in payloads]


@pytest.mark.usefixtures("large_row")
def test_reads_are_bounded_by_read_max_not_by_connected_files(tmp_path, monkeypatch):
    at(monkeypatch, "large")
    opened, sizes = {}, {}
    for connected in (50, 500):
        with monkeypatch.context() as m:
            opened[connected], sizes[connected] = ask_over(tmp_path, m, connected)
    # the same number of files is opened with 50 notes connected and with 500: READ_MAX, however the set grows
    assert len(opened[50]) == len(opened[500]) == ask.READ_MAX
    # each is read up to the preset's size and no further: the most one ask reads is READ_MAX times that
    for connected, chars in sizes.items():
        assert len(chars) == ask.READ_MAX and max(chars) <= PRESETS["large"], connected
        assert max(chars) > ask.READ_CHARS, connected  # the preset's size applies to every file read, not small's
        assert sum(chars) <= ask.READ_MAX * PRESETS["large"], connected


#: What the offline fetch hook below reports for each request, measured the way the batcher and the client measure a call:
#: judge tokens are UTF-8 bytes over the profile's bytes per token, rounded up; the batcher keeps a state plus its questions
#: (each with 20 of overhead) under the profile's call budget.
TOKENS_HOOK = r"""
const fs = require('node:fs');
const seen = { requests: [] };
process.on('exit', () => fs.writeFileSync(process.env.SJ_SEEN, JSON.stringify(seen)));
const tok = v => Math.ceil(Buffer.byteLength(JSON.stringify(v), 'utf8') / Number(process.env.SJ_BYTES_PER_TOKEN));
global.fetch = async (_url, opts) => {
  const payload = JSON.parse(opts.body);
  const questions = Object.values(payload.questions);
  seen.requests.push({ questions: questions.length, state: tok(payload.state), longest: Math.max(...questions.map(tok)),
    all: questions.reduce((sum, q) => sum + tok(q) + 20, 0) });
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_none')) || keys[0];
    return [id, { type: 'choice', choice, confidence: 1, probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 1 : 0])) }];
  }));
  return new Response(JSON.stringify({ model: 'offline', answers }), { status: 200 });
};
"""


@pytest.mark.usefixtures("large_row")
def test_every_payload_fits_its_presets_call_room(tmp_path, monkeypatch):
    nav = (REPO / "src" / "enhance" / "navigation.ts").read_text()
    max_leaves = int(re.search(r"const MAX_LEAVES = (\d+);", nav).group(1))  # the most passages the navigation takes
    cap = int(re.search(r"const MAX_INPUT_BYTES = (\d+) \* 1024 \* 1024;", (REPO / "src" / "navigation-cli.ts").read_text()).group(1)) << 20
    ceiling = judge_profile.load().file_ceiling_bytes
    # the heaviest notes a check meets: the largest that connects, in plain text, and one at three bytes to the character
    notes = {"plain": {"name": "harbor.md", "text": note(ceiling, 0.5), "question": QUESTION},
             "japanese": {"name": "minato.md", "text": japanese_note(130_000), "question": QUESTION}}
    node = shutil.which("node")
    hook = tmp_path / "tokens.cjs"
    hook.write_text(TOKENS_HOOK)
    for preset, chars in PRESETS.items():
        prof = judge_profile.load(context=preset)
        # the profile's numbers: a read's passages and their questions (about 1.45 times the characters) fit the call budget
        assert prof.read_chars == chars and chars * 1.45 <= prof.call_tokens * prof.bytes_per_token and chars <= prof.file_ceiling_bytes
        at(monkeypatch, preset)
        payloads = {}
        for kind, case in notes.items():
            done, ctx = start(tmp_path, case)
            assert done is None, (preset, kind)
            payloads[kind] = ctx["payload"]
            assert len(sent(ctx)) <= max_leaves, (preset, kind)  # inside the navigation's own passage cap
            assert filled(ask.split_passages(case["text"]), ctx["picked"], chars), (preset, kind)  # and it reads the preset's size
        # ten notes of the heaviest kind in one run stay under the input cap navigation-cli accepts
        assert 10 * len(json.dumps(payloads["japanese"]).encode()) < cap, preset
        if not node:
            continue  # the rest runs the real navigation-cli
        seen = tmp_path / f"seen-{preset}.json"
        monkeypatch.setenv(ask.judges.key_env(), "offline")  # fetch is replaced below: the key only has to be set
        monkeypatch.setenv("NODE_OPTIONS", f"--require={TABLE_HOOK} --require={hook}")
        monkeypatch.setenv("SJ_SEEN", str(seen))
        monkeypatch.setenv("SJ_BYTES_PER_TOKEN", str(prof.bytes_per_token))
        body, error = ask.run_navigation({"batch": list(payloads.values()), **({} if preset == "small" else {"context": preset})})
        assert error is None and [r["status"] for r in body["results"]] == ["candidates"] * 2, (preset, error, body)
        requests = json.loads(seen.read_text())["requests"]
        assert len(requests) == 2, (preset, requests)  # one call for each note: every passage of it rides in one request
        for r in requests:
            assert r["state"] + r["all"] <= prof.call_tokens, (preset, r, prof.call_tokens)  # what the batcher measures
            assert r["state"] + r["longest"] <= prof.call_tokens, (preset, r, prof.call_tokens)  # what the client measures


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-golden"]:
        sys.exit(__doc__)
    import tempfile
    with tempfile.TemporaryDirectory() as d, pytest.MonkeyPatch.context() as mp:
        data = golden_data(Path(d), mp)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(dump(data))
    print(f"wrote {GOLDEN} ({len(data['payloads'])} payloads)")
