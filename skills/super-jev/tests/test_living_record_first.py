#!/usr/bin/env python3
"""Living records first, the matched section, and the two review follow-ups ("may", "the <Name>"), on a made-up
user (Rosa, her son Teo) in a temp folder. Pure functions plus word_search over a fake candidate list; no state, no
provider calls.

    python3 -m pytest skills/super-jev/tests/test_living_record_first.py -q
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask  # noqa: E402

FOLKS = {"rosa": {"self"}, "teo": {"son"}}

LIVING = """# Rosa - current supplements (living record)
Entry rule: one line per supplement: name, amount, when taken, started.
Rules: edit Current in place; add a Change log line for every change.

## Current
- vitamin D 1000 IU, morning, started 2026-01-04
- magnesium 200 mg, night, started 2026-03-11

## Change log
- 2026-03-11 added magnesium
"""
DATED = """# 2026-05-02 supplements started - magnesium
Started magnesium 200 mg at night, supplements note from the visit. Supplements supplements magnesium.
"""
NOTES = """# Garden notes
## Tomatoes
Water the tomatoes every morning.
## Ivy
Trim the ivy in spring.
"""


def _files(tmp):
    d = tmp / "rosa" / "health" / "supplements"
    d.mkdir(parents=True)
    out = []
    for name, text in (("current.md", LIVING), ("2026-05-02-magnesium.md", DATED)):
        (d / name).write_text(text)
        out.append(("rosa-health", str(d / name), {"sha256": hashlib.sha256(text.encode()).hexdigest()}))
    (tmp / "garden.md").write_text(NOTES)
    return out


def test_living_record_shape(tmp_path):
    files = _files(tmp_path)
    assert ask.living_record(files[0][1]) is True
    assert ask.living_record(files[1][1]) is False
    assert ask.living_record(str(tmp_path / "garden.md")) is False  # headed sections, but no entry rule


def test_living_record_ranks_first_when_its_kind_is_asked(tmp_path):
    files = _files(tmp_path)
    got = ask.word_search("what supplements is rosa on", ["rosa-health"], candidates=files, read_paths={p for _, p, _ in files})
    assert got and got[0][1] == files[0][1], got
    assert files[1][1] in [p for _, p, _ in got]  # the dated file is still offered, below it


def test_matched_section(tmp_path):
    files = _files(tmp_path)
    assert ask.matched_section(files[0][1], "what supplements is rosa on", living=True) == "Current"
    assert ask.matched_section(files[0][1], "rosa supplements change log", living=True) == "Change log"
    assert ask.matched_section(str(tmp_path / "garden.md"), "when do I trim the ivy") == "Ivy"
    assert ask.matched_section(files[1][1], "magnesium") is None  # no headed sections


def test_may_is_a_month_only_as_a_date():
    assert ask.question_dates("notes from may 5") == {(None, 5, 5)}
    assert ask.question_dates("how may 2 doses be taken") == set()
    assert ask.question_dates("it may 3x the dose") == set()
    assert ask.question_dates("what may I take") == set()


def test_the_name_still_means_the_person():
    assert ask.question_people("open the Teo file", FOLKS) == {"teo"}
    assert ask.question_people("my teo plant", FOLKS) == {"rosa"}  # "my <word>" stays a thing


def test_possessive_without_apostrophe_is_the_relation():
    assert ask.question_people("I need my sons list", FOLKS) == {"teo"}


def test_kind_named_by_a_short_plural_or_a_longer_form(tmp_path):
    files = _files(tmp_path)
    assert ask.kind_stem("sups") == "sup" and ask.kind_stem("supplementation") == "suppl"
    assert ask.kind_stem("bus") == "" and ask.kind_stem("glass") == ""
    for q in ("rosa sups", "rosa supplementation list"):  # neither word is in the list itself
        got = ask.word_search(q, ["rosa-health"], candidates=files, read_paths={p for _, p, _ in files})
        assert got and got[0][1] == files[0][1], (q, got)


def test_name_boost_is_exact_word_or_plural_not_a_loose_stem():
    # "latest" must not boost a file named "...-late-update.md" over the one the words actually fit
    import tempfile  # not tmp_path: its folder name (this test's name) would add path words
    d = Path(tempfile.mkdtemp()) / "teo" / "health"
    d.mkdir(parents=True)
    texts = {"timeline.md": "# Teo timeline\n- checkup\n- flu shot\nlatest items.\n",
             "2026-09-08-late-update.md": "# Late update\nTeo timeline items, late items, items noted latest.\n"}
    files = []
    for n, t in texts.items():
        (d / n).write_text(t)
        files.append(("teo-health", str(d / n), {"sha256": hashlib.sha256(t.encode()).hexdigest()}))
    got = ask.word_search("latest items in the timeline for teo", ["teo-health"], candidates=files,
                          read_paths={p for _, p, _ in files})
    assert got[0][1].endswith("timeline.md"), got


def test_no_living_lift_when_the_question_names_a_date(tmp_path):
    files = _files(tmp_path)
    got = ask.word_search("rosa supplements started 2026-05-02", ["rosa-health"], candidates=files,
                          read_paths={p for _, p, _ in files})
    assert got[0][1] == files[1][1], got  # the dated file for that day, not the living record
