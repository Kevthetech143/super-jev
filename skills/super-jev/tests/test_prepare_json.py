#!/usr/bin/env python3
"""Frozen contract tests (2026-10-01), contract item C1, PR 2: `prepare_bulk.py --json`.

With --json, a connect prints exactly one JSON object on stdout (schema v: 1) and exits with the code
text mode gives: 0 all connected, 1 something failed, 2 refused, 3 something held. The object is
{connected, held [{path, why}], failed [{path, why}], skipped [{kind, what, count, way_in}], refused {kind, why}};
a field with nothing in it is left out, and nothing in it is a score or a note's text. PR 2c: every skipped
row and every refusal also carries a stable `kind` (a short fixed word list), so a program words them from
the kind and never parses the free text. A `too_many` refusal also carries whole numbers `count` and `max`, so the
app shows "count is more than one connect takes (max)" without parsing `why`. Text mode
prints from the same result, so the two are held together here. Made-up company Quillbrook, made-up
user "sam"; no network, no real key, no Jev call.

    python3 -m pytest skills/super-jev/tests/test_prepare_json.py -q
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pb_json", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

OK = {"state": "SUPPORTED", "confidence": 0.95, "secs": 0}
COUNTS = re.compile(r"^CONNECTED (\d+), HELD (\d+), FAILED (\d+)\b", re.M)
TOKEN = "ghp_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ3kLm"  # made up, split so a secret scan does not flag this file
OTHER_TYPES = "file(s) of other types"  # the connect SKIP rule's own words


def folder(tmp_path, **files):
    root = tmp_path / "Quillbrook Notes"  # a path with a space
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def mixed(tmp_path):
    """The DESIGN case: 2 notes, 1 code file, 1 note holding a made-up token."""
    return folder(tmp_path, **{"warranty.md": "# Warranty\n\nParts are covered for 24 months.\n",
                               "returns.md": "# Returns\n\nItems come back within 30 days.\n",
                               "calc.py": "x = 1\n",
                               "keys.md": f"# Keys\n\nThe deploy token is {TOKEN}\n"})


@pytest.fixture
def fake(monkeypatch):
    """A fake engine: every note drafts and passes, every part connects. Returns the connect calls."""
    calls = []
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda *a, **k: OK)
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [OK for _ in claims], raising=False)
    monkeypatch.setattr(pb, "connect_part", lambda pointer, principals, files, cache, shareable=False:
                        calls.append(pointer) or {"connected": True})
    return calls


@pytest.fixture
def go(tmp_path, monkeypatch, capfd):
    """run(root, *extra, json_mode=True) -> (exit code, stdout). Each run has its own prepare-cache."""
    n = [0]

    def run(root, *extra, json_mode=True, pointer="qb"):
        n[0] += 1
        monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / f"cache{n[0]}")
        argv = ["prepare_bulk.py", "--root", str(root), "--pointer", pointer, "--principal", "sam",
                "--no-findability", "--no-shared", "--writer", "claude", *extra]
        monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_mode else []))
        capfd.readouterr()
        code = pb.main()
        return code, capfd.readouterr().out
    return run


def one_object(out):
    """stdout is exactly one line holding exactly one JSON object, v: 1."""
    assert out.endswith("\n") and len(out.splitlines()) == 1, out
    obj = json.loads(out)
    assert isinstance(obj, dict) and obj["v"] == 1
    return obj


def test_clean_connect_is_one_object_exit_0_and_empty_fields_are_left_out(tmp_path, fake, go):
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nParts are covered.\n", "returns.md": "# Returns\n\nBack in 30 days.\n"}))
    obj = one_object(out)
    assert code == 0
    assert obj == {"v": 1, "connected": 2}
    assert fake == ["qb"]


def test_mixed_folder_holds_one_path_only_skips_one_with_its_way_in_and_exits_3(tmp_path, fake, go):
    code, out = go(mixed(tmp_path))
    obj = one_object(out)
    assert code == 3
    assert obj["connected"] == 2
    assert set(obj) == {"v", "connected", "held", "skipped"}
    assert len(obj["held"]) == 1 and set(obj["held"][0]) == {"path", "why"}
    assert obj["held"][0]["path"].endswith("keys.md") and obj["held"][0]["why"]
    assert TOKEN not in out and "deploy token" not in out
    [row] = obj["skipped"]
    assert set(row) == {"kind", "what", "count", "way_in"} and row["count"] == 1 and row["kind"] == "types"
    assert row["what"] == f"{OTHER_TYPES} (.py 1)" and ".md" in row["way_in"]


def test_over_max_files_is_refused_with_its_reason_and_exit_2_and_no_work(tmp_path, fake, go):
    root = folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(3)})
    code, out = go(root, "--max-files", "2")
    obj = one_object(out)
    assert code == 2
    assert obj == {"v": 1, "connected": 0,
                   "refused": {"kind": "too_many", "why": obj["refused"]["why"], "count": 3, "max": 2}}
    assert "3 files exceed" in obj["refused"]["why"]
    assert fake == []


def test_a_part_that_will_not_connect_fails_each_of_its_notes_with_a_reason_and_exits_1(tmp_path, monkeypatch, fake, go):
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False, "why": "the engine refused it"})
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}))
    obj = one_object(out)
    assert code == 1
    assert obj["connected"] == 0 and "held" not in obj and "refused" not in obj
    assert sorted(Path(f["path"]).name for f in obj["failed"]) == ["returns.md", "warranty.md"]
    assert all(f["why"] == "the engine refused it" for f in obj["failed"])


def test_a_part_with_no_reason_still_gets_a_plain_one(tmp_path, monkeypatch, fake, go):
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n"}))
    obj = one_object(out)
    assert code == 1 and obj["failed"][0]["why"].strip()


def test_a_note_the_check_rejects_fails_alone_with_no_score_and_none_of_the_judges_words(tmp_path, monkeypatch, fake, go):
    bad = {"state": "NOT_SUPPORTED", "confidence": 0.31, "secs": 0, "reason": "quoted XYZZY from the note"}
    monkeypatch.setattr(pb, "gate", lambda desc, path: bad if path.endswith("returns.md") else OK)
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}))
    obj = one_object(out)
    assert code == 1 and obj["connected"] == 1
    [row] = obj["failed"]
    assert row["path"].endswith("returns.md") and row["why"]
    assert "XYZZY" not in out and "0.31" not in out and "confidence" not in out and "score" not in out


def test_a_note_the_writer_drafts_nothing_for_fails_with_a_reason(tmp_path, monkeypatch, fake, go):
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {})
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n"}))
    obj = one_object(out)
    assert code == 1 and obj["connected"] == 0
    assert obj["failed"][0]["path"].endswith("warranty.md") and "draft" in obj["failed"][0]["why"]


def test_a_folder_with_no_markdown_fails_on_the_folder_and_still_says_what_was_left_out(tmp_path, fake, go):
    root = folder(tmp_path, **{"calc.py": "x = 1\n", "data.csv": "a,b\n"})
    code, out = go(root)
    obj = one_object(out)
    assert code == 1 and obj["connected"] == 0
    assert obj["failed"][0]["path"] == str(root) and ".md" in obj["failed"][0]["why"]
    assert obj["skipped"] == [{"kind": "types", "what": f"{OTHER_TYPES} (.csv 1, .py 1)", "count": 2,
                               "way_in": "only .md files connect"}]


def test_a_writer_that_fails_fails_the_run_with_its_reason_and_exits_1(tmp_path, monkeypatch, fake, go):
    def broken(items, *a, **k):
        raise pb.WriterError("writer exited with status 1")
    monkeypatch.setattr(pb, "writer", broken)
    root = folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n"})
    code, out = go(root)
    obj = one_object(out)
    assert code == 1 and obj["failed"] == [{"path": str(root), "why": "description writer failed: writer exited with status 1"}]


def test_a_crash_is_still_one_object_and_exit_1(tmp_path, monkeypatch, fake, go, capfd):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(pb, "connect_part", boom)
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n"}))
    obj = one_object(out)
    assert code == 1 and obj["failed"] and "boom" in obj["failed"][0]["why"]


def test_a_missing_folder_is_refused_and_exit_2(tmp_path, fake, go):
    code, out = go(tmp_path / "nowhere")
    obj = one_object(out)
    assert code == 2 and obj["refused"]["why"].startswith("--root is not a folder") and "failed" not in obj


def test_missing_required_flags_are_refused_in_json_not_left_to_argparse_text(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--json", "--root", str(tmp_path)])
    code = pb.main()
    obj = one_object(capfd.readouterr().out)
    assert code == 2 and "--principal" in obj["refused"]["why"]


def test_an_argument_error_under_json_is_one_refused_object_and_exit_2(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--json", "--root", str(tmp_path), "--limit", "many"])
    with pytest.raises(SystemExit) as e:
        pb.main()
    obj = one_object(capfd.readouterr().out)
    assert e.value.code == 2 and "--limit" in obj["refused"]["why"]


def test_an_argument_error_without_json_is_still_argparse_text_on_stderr(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(tmp_path), "--limit", "many"])
    with pytest.raises(SystemExit) as e:
        pb.main()
    cap = capfd.readouterr()
    assert e.value.code == 2 and cap.out == "" and "usage:" in cap.err


def test_json_does_not_cover_list_so_it_is_refused_not_printed_as_a_table(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--json", "--list", "--pointer", "qb"])
    code = pb.main()
    obj = one_object(capfd.readouterr().out)
    assert code == 2 and "--list" in obj["refused"]["why"]


def test_a_held_note_whose_name_carries_a_secret_shows_the_name_without_it(tmp_path, fake, go):
    root = folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "password-hunter2xyz-notes.md": "# Notes\n\nPlain words.\n"})
    code, out = go(root)
    obj = one_object(out)
    assert code == 3 and "hunter2xyz" not in out
    assert obj["held"][0]["path"].endswith("password-[REDACTED].md")


def scenario_clean(tmp_path, monkeypatch):
    return folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}), []


def scenario_mixed(tmp_path, monkeypatch):
    return mixed(tmp_path), []


def scenario_part_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False, "why": "no"})
    return folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}), []


def scenario_check_rejects_one(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "gate", lambda desc, path: {"state": "NOT_SUPPORTED", "confidence": 0.3, "secs": 0}
                        if path.endswith("returns.md") else OK)
    return folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}), []


def scenario_refused(tmp_path, monkeypatch):
    return folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(3)}), ["--max-files", "2"]


@pytest.mark.parametrize("scenario", [scenario_clean, scenario_mixed, scenario_part_fails, scenario_check_rejects_one,
                                      scenario_refused])
def test_text_and_json_agree_on_exit_code_and_on_every_count(scenario, tmp_path, monkeypatch, fake, go):
    root, extra = scenario(tmp_path, monkeypatch)
    text_code, text_out = go(root, *extra, json_mode=False)
    json_code, json_out = go(root, *extra, json_mode=True)
    obj = one_object(json_out)
    assert json_code == text_code
    assert text_code == {"scenario_clean": 0, "scenario_mixed": 3, "scenario_part_fails": 1,
                         "scenario_check_rejects_one": 1, "scenario_refused": 2}[scenario.__name__]
    assert not any(line.startswith('{"v"') for line in text_out.splitlines())  # text mode prints no JSON
    m = COUNTS.search(text_out)
    if m:
        assert (obj["connected"], len(obj.get("held", [])), len(obj.get("failed", []))) == tuple(map(int, m.groups()))
        assert "refused" not in obj
    else:
        assert text_code == 2 and f"REFUSED: {obj['refused']['why']}" in text_out
    # a non-zero code always comes with something the person can read
    if json_code:
        assert obj.get("refused") or obj.get("failed") or obj.get("held")


def test_text_mode_keeps_its_last_line_its_held_lines_and_its_exit_code(tmp_path, fake, go):
    code, out = go(mixed(tmp_path), json_mode=False)
    assert code == 3 and out.rstrip().splitlines()[-1].startswith("CONNECTED 2, HELD 1, FAILED 0")
    assert "  HELD  keys.md  (card/password-like text;" in out


def test_skipped_rows_are_the_skip_lines_text_mode_prints_one_for_one(tmp_path, fake, go):
    """Every reason the connect left files out for is one SKIP line in text and one row in --json, from the
    same table, so the two cannot say different things (and a reason with no way in has no way_in)."""
    root = folder(tmp_path, **{"notes.md": "# Notes\n\nText.\n", "calc.py": "x = 1\n", "data.txt": "a\n",
                               ".hidden.md": "# H\n\nh\n", "blank.md": "\n",
                               "superjev-test-run.md": "# Run\n\nQuestions.\n", "superjev-test-two.md": "# Two\n\nQ.\n"})
    (root / "node_modules").mkdir()
    (root / "node_modules" / "n.md").write_text("# N\n\nn\n")
    text_code, text = go(root, json_mode=False)
    obj = one_object(go(root)[1])
    assert text_code == 0 and obj["connected"] == 1
    # the line text mode already printed for test/scratch files, unchanged
    assert ("  SKIP  2 test/scratch output file(s) (e.g. *superjev-test*, ops/sj*/); "
            "name one exactly with --name to connect it") in text
    from_rows = [f"  SKIP  {r['count']} {r['what']}" + (f"; {r['way_in']}" if "way_in" in r else "") for r in obj["skipped"]]
    assert len(from_rows) == 5 and from_rows == [x for x in text.splitlines() if x.startswith("  SKIP  ")]
    assert [r["what"] for r in obj["skipped"] if "way_in" not in r] == ["empty .md file(s)"]


def test_a_refusal_carries_only_why_even_when_notes_were_held_or_left_out(tmp_path, fake, go):
    """A refused run did nothing, so the app can show the refusal alone, never "Held back" under "Not connected"."""
    root = folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(3)})
    (root / "keys.md").write_text(f"# Keys\n\nThe deploy token is {TOKEN}\n")
    (root / "calc.py").write_text("x = 1\n")
    code, text = go(root, "--max-files", "2", json_mode=False)
    assert code == 2 and "  HELD  keys.md" in text and "  SKIP  1 file(s) of other types" in text
    code, out = go(root, "--max-files", "2")
    obj = one_object(out)
    assert code == 2 and set(obj) == {"v", "connected", "refused"} and obj["connected"] == 0


def test_when_every_note_is_held_nothing_connected_exit_1_and_connected_is_the_signal(tmp_path, fake, go):
    """Exit 1 with held notes and no failed row (as in text mode: CONNECTED 0, HELD 1, FAILED 0). The app
    reads connected == 0, not a failed row."""
    root = folder(tmp_path, **{"keys.md": f"# Keys\n\nThe deploy token is {TOKEN}\n"})
    code, text = go(root, json_mode=False)
    assert code == 1 and "CONNECTED 0, HELD 1, FAILED 0" in text
    code, out = go(root)
    obj = one_object(out)
    assert code == 1 and obj["connected"] == 0 and set(obj) == {"v", "connected", "held"} and TOKEN not in out
    assert fake == []


def test_a_crash_after_one_part_connected_still_reports_it(tmp_path, monkeypatch, fake, go):
    calls = []

    def second_part_crashes(pointer, principals, files, cache, shareable=False):
        calls.append(pointer)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return {"connected": True}
    monkeypatch.setattr(pb, "connect_part", second_part_crashes)
    code, out = go(folder(tmp_path, **{"warranty.md": "# Warranty\n\nCovered.\n", "returns.md": "# Returns\n\nBack.\n"}),
                   "--limit", "1")
    obj = one_object(out)
    assert code == 1 and calls == ["qb", "qb-2"] and obj["connected"] == 1 and "boom" in obj["failed"][0]["why"]


def test_real_engine_free_connect_json_with_the_builtin_writer_and_no_key(tmp_path, monkeypatch, capfd):
    """R3: a real connect (no key, no model call): 2 notes connect, the note with a token is held, the code
    file is left out, and the pointer is really registered."""
    state = tmp_path / "state"
    (state / "_memory").mkdir(parents=True)
    (state / "_memory" / "config.json").write_text(json.dumps({"db": "memory.sqlite3", "registry": "registry.json"}))
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUPERJEV_REPO", raising=False)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "gate", lambda *a, **k: pytest.fail("a free connect must not call the judge"))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(mixed(tmp_path)), "--pointer", "qb", "--principal", "sam",
                                      "--writer", "builtin", "--no-shared", "--json"])
    code = pb.main()
    obj = one_object(capfd.readouterr().out)
    assert code == 3
    assert obj["connected"] == 2 and len(obj["held"]) == 1 and obj["held"][0]["path"].endswith("keys.md")
    assert obj["skipped"][0]["count"] == 1 and TOKEN not in json.dumps(obj)
    assert '"qb"' in (state / "_memory" / "registry.json").read_text()


# ---- PR 2c: every skipped row and every refusal carries a stable `kind` --------------------------------

# The documented word lists. A program words a skipped row or a refusal from its kind; a kind it does not
# know is shown by its `what` / `why`. Each skip kind is a SKIP_REASONS key and maps to the start of that
# reason's own words.
SKIP_KINDS = {
    "link": "linked file(s) point outside",
    "name": ".md file(s) with backup or credential-style names",
    "folder": ".md file(s) in folders skipped by default",
    "folder_other": "file(s) of other types in folders skipped by default",
    "hidden": "hidden .md file(s)",
    "dataset": "file(s) in prepared dataset copies",
    "test": "test/scratch output file(s)",
    "worktree": "file(s) inside git worktree copies",
    "empty": "empty .md file(s)",
    "types": "file(s) of other types (",
}
REFUSAL_KINDS = {"too_many", "not_a_folder", "usage"}


def every_reason_folder(tmp_path):
    """(root, extra args): a folder, plus a second root that is a prepared dataset copy, that together make
    the connect leave something out for each of the ten default reasons."""
    dataset = tmp_path / ".local" / "retrieval-datasets" / "Quillbrook Passages"
    dataset.mkdir(parents=True)
    (dataset / "passages.md").write_text("# Passages\n\nCopied text.\n")      # dataset
    root = folder(tmp_path)
    (root / "notes.md").write_text("# Notes\n\nText.\n")
    (root / "calc.py").write_text("x = 1\n")                                   # types
    (root / ".hidden.md").write_text("# H\n\nh\n")                             # hidden
    (root / "blank.md").write_text("\n")                                       # empty
    (root / "superjev-test-run.md").write_text("# Run\n\nQuestions.\n")        # test
    (root / "notes.bak.md").write_text("# Old\n\nOld text.\n")                 # name
    (root / "node_modules").mkdir()
    (root / "node_modules" / "n.md").write_text("# N\n\nn\n")                  # folder
    (root / "node_modules" / "data.csv").write_text("a,b\n")                   # folder_other
    (root / "wt").mkdir()
    (root / "wt" / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    (root / "wt" / "copy.md").write_text("# Copy\n\nA stale copy.\n")          # worktree
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "far.md").write_text("# Far\n\nOutside.\n")
    (root / "far-link.md").symlink_to(tmp_path / "elsewhere" / "far.md")       # link
    return root, ["--root", str(dataset)]


def test_every_default_skip_reason_carries_its_documented_kind(tmp_path, fake, go):
    root, extra = every_reason_folder(tmp_path)
    code, out = go(root, *extra)
    obj = one_object(out)
    assert code == 0 and obj["connected"] == 1
    by_kind = {r["kind"]: r for r in obj["skipped"]}
    assert len(by_kind) == len(obj["skipped"]) and set(by_kind) == set(SKIP_KINDS)
    for kind, words in SKIP_KINDS.items():
        row = by_kind[kind]
        assert row["what"].startswith(words), (kind, row)
        assert set(row) <= {"kind", "what", "count", "way_in"} and row["count"] >= 1


def test_the_text_skip_lines_do_not_change_and_never_show_a_kind(tmp_path, fake, go):
    root, extra = every_reason_folder(tmp_path)
    obj = one_object(go(root, *extra)[1])
    text = [x for x in go(root, *extra, json_mode=False)[1].splitlines() if x.startswith("  SKIP  ")]
    assert text == [f"  SKIP  {r['count']} {r['what']}" + (f"; {r['way_in']}" if "way_in" in r else "") for r in obj["skipped"]]
    assert not any(word in " ".join(text) for word in ("kind", "folder_other"))


def test_the_skip_kinds_are_a_fixed_list_that_is_documented_next_to_the_schema():
    """A new reason must be added here and to the docstring: one list, no silent extra word."""
    assert set(pb.SKIP_REASONS) == set(SKIP_KINDS)
    for kind in set(SKIP_KINDS) | REFUSAL_KINDS:
        assert f"`{kind}`" in pb.__doc__, kind


def run_main(monkeypatch, capfd, cache, *args):
    monkeypatch.setattr(pb, "CACHE_DIR", cache)
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--principal", "sam", "--no-findability", "--no-shared",
                                      "--writer", "claude", "--json", *args])
    capfd.readouterr()
    try:
        code = pb.main()
    except SystemExit as e:  # an argument error leaves through argparse
        code = e.code
    return code, one_object(capfd.readouterr().out)


@pytest.mark.parametrize("kind, args", [
    ("too_many", ["--max-files", "2"]),
    ("not_a_folder", None),
    ("usage", ["--limit", "51"]),
    ("usage", ["--limit", "many"]),
    ("usage", ["--writer-command", "'"]),
    ("usage", ["--writer-command", "  "]),
    ("usage", ["--list", "--pointer", "qb"]),
], ids=["too-many-files", "not-a-folder", "limit-over-50", "argument-error", "bad-quote", "blank-command", "json-with-list"])
def test_each_refusal_gives_its_documented_kind_and_its_reason(kind, args, tmp_path, monkeypatch, fake, capfd):
    root = folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(3)})
    base = ["--root", str(root if args is not None else tmp_path / "nowhere"), "--pointer", "qb"]
    code, obj = run_main(monkeypatch, capfd, tmp_path / "cache", *base, *(args or []))
    assert code == 2 and set(obj) == {"v", "connected", "refused"} and obj["connected"] == 0
    assert set(obj["refused"]) == {"kind", "why"} | ({"count", "max"} if kind == "too_many" else set())
    assert obj["refused"]["why"].strip() and obj["refused"]["kind"] == kind and kind in REFUSAL_KINDS
    assert fake == []


def test_missing_required_flags_are_a_usage_refusal(tmp_path, monkeypatch, capfd):
    code, obj = run_main(monkeypatch, capfd, tmp_path / "cache", "--root", str(tmp_path))
    assert code == 2 and obj["refused"]["kind"] == "usage" and "--pointer" in obj["refused"]["why"]


def test_a_refresh_that_needs_too_many_drafts_is_the_same_too_many_kind(tmp_path, monkeypatch, fake, capfd):
    root = folder(tmp_path, **{"n0.md": "# Note 0\n\nText 0.\n"})
    cache = tmp_path / "cache"
    assert run_main(monkeypatch, capfd, cache, "--root", str(root), "--pointer", "qb")[0] == 0
    for i in range(1, 4):
        (root / f"n{i}.md").write_text(f"# Note {i}\n\nText {i}.\n")
    code, obj = run_main(monkeypatch, capfd, cache, "--root", str(root), "--pointer", "qb", "--refresh", "--max-files", "2")
    assert code == 2 and obj["refused"]["kind"] == "too_many" and "need drafting" in obj["refused"]["why"]
    assert (obj["refused"]["count"], obj["refused"]["max"]) == (3, 2)  # the 3 new notes, not the 4 in the folder


def test_too_many_carries_its_numbers_as_whole_numbers_the_app_can_show_without_the_text(tmp_path, monkeypatch, fake, capfd):
    """"1,230 notes is more than one connect takes (250)": count and max, never parsed out of `why`."""
    root = folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(5)})
    code, obj = run_main(monkeypatch, capfd, tmp_path / "cache", "--root", str(root), "--pointer", "qb", "--max-files", "4")
    refused = obj["refused"]
    assert code == 2 and refused["kind"] == "too_many" and type(refused["count"]) is int and type(refused["max"]) is int
    assert (refused["count"], refused["max"]) == (5, 4)
    assert "5 files exceed --max-files 4" in refused["why"]
    assert "`count`" in pb.__doc__ and "`max`" in pb.__doc__


def test_an_unexpected_error_while_replaying_a_recipe_is_an_internal_failure_not_a_usage_refusal(
        tmp_path, monkeypatch, fake, capfd):
    """Any error while replaying a recipe is the engine's own fault, so it is
    a failure (exit 1, as in text mode), never blamed on the user's arguments."""
    def boom(a):
        raise ValueError("recipe unreadable")
    monkeypatch.setattr(pb, "replay_recipe", boom)
    root = folder(tmp_path, **{"a.md": "# A\n\nText.\n"})
    code, obj = run_main(monkeypatch, capfd, tmp_path / "cache", "--root", str(root), "--pointer", "qb", "--refresh")
    assert code == 1 and "refused" not in obj
    assert obj["failed"][0]["why"] == "internal error: ValueError: recipe unreadable"


def test_text_mode_keeps_its_refused_line_with_no_kind(tmp_path, fake, go):
    root = folder(tmp_path, **{f"n{i}.md": f"# Note {i}\n\nText {i}.\n" for i in range(3)})
    code, text = go(root, "--max-files", "2", json_mode=False)
    assert code == 2 and "REFUSED: 3 files exceed --max-files 2;" in text and "too_many" not in text


def test_a_kind_this_version_does_not_list_still_comes_through_as_one_valid_object(tmp_path, monkeypatch, capfd):
    """No closed list is checked while printing: a reason or refusal added later carries its own kind, so a
    reader that does not know it can still show `what` / `why`, and v stays 1."""
    monkeypatch.setitem(pb.SKIP_REASONS, "future", ("future thing(s) ({names})", "ask again later"))
    assert pb.skip_rows({"future": {".xyz": 2}}) == [
        {"kind": "future", "what": "future thing(s) (.xyz 2)", "count": 2, "way_in": "ask again later"}]
    monkeypatch.setattr(pb, "_RESULT", {})
    assert pb.refuse("future", "a reason from a later version") == 2
    assert json.loads(json.dumps(pb.result_object())) == {
        "v": 1, "connected": 0, "refused": {"kind": "future", "why": "a reason from a later version"}}
