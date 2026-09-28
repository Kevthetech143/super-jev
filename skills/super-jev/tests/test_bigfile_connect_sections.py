#!/usr/bin/env python3
"""Offline tests for connecting a file over the size ceiling in sections (2026-09-28): the file
is cut into line ranges at natural edges (headings, top-level def/class), each section is
drafted, gated and connected like a file of its own, the file on disk is never changed, and a
refresh re-gates only the sections whose text changed. The connect runs through the real local
connector (path_connect) with a temporary registry; no network and no real key.

    python3 -m pytest skills/super-jev/tests/test_bigfile_connect_sections.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(REPO / "experiments" / "verified-pointer-memory"))
_spec = importlib.util.spec_from_file_location("pb_sections", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pb)
import path_connect  # noqa: E402


def code_file(n=60, body=6):
    return "#!/usr/bin/env python3\n\"\"\"Module.\"\"\"\n\n" + "".join(
        f"@decorated\ndef function_{i}(a, b):\n" + "".join(f"    value_{i}_{k} = a + b + {k}\n" for k in range(body))
        + f"    return value_{i}_0\n\n\n" for i in range(n))


def notes_file(n=40, body=12):
    return "# Notes\n\nIntro.\n\n" + "".join(
        f"## Topic {i}\n\n" + "".join(f"- fact {i}.{k} about topic {i}\n" for k in range(body)) + "\n"
        for i in range(n))


@pytest.fixture
def small(monkeypatch):
    """Section sizes scaled down so small fixtures hold many sections."""
    monkeypatch.setattr(pb, "SECTION_MIN", 600)
    monkeypatch.setattr(pb, "SECTION_MAX", 2000)
    monkeypatch.setattr(pb, "SECTION_EDGE_MOD", 2)


# --- cutting -------------------------------------------------------------------------------

@pytest.mark.parametrize("text, suffix", [(code_file(), ".py"), (notes_file(), ".md"), ("x = 1\n" * 2000, ".py"),
                                          ("one line without an end", ".md"), ("a\n" * 5, ".md")],
                         ids=["code", "notes", "no-edges", "one-line", "tiny"])
def test_sections_cover_the_file_in_order_within_bounds(small, text, suffix):
    secs = pb.split_sections(text, suffix)
    assert secs[0][0] == 1 and secs[-1][1] == len(pb.file_lines(text))
    assert all(a[1] + 1 == b[0] for a, b in zip(secs, secs[1:]))
    assert "".join(pb.section_text(text, s) for s in secs) == text
    sizes = [len(pb.section_text(text, s).encode()) for s in secs]
    assert all(n <= pb.SECTION_MAX for n in sizes)
    assert all(n >= pb.SECTION_MIN for n in sizes[:-1])


def test_code_is_cut_before_a_top_level_def_and_its_decorator(small):
    text = code_file()
    secs = pb.split_sections(text, ".py")
    lines = pb.file_lines(text)
    assert len(secs) > 3
    assert all(lines[a - 1].startswith("@decorated") for a, _ in secs[1:])


def test_notes_are_cut_at_headings_but_never_inside_a_code_fence(small):
    fenced = "```\n" + "".join(f"# not a heading {k}\n" for k in range(200)) + "```\n"
    text = notes_file(10) + fenced + notes_file(10)
    lines = pb.file_lines(text)
    heads = [lines[a - 1] for a, _ in pb.split_sections(text, ".md")[1:]]
    assert heads and any(h.startswith("## Topic") for h in heads)
    # A fenced "# ..." line is never a heading edge (it can only be a size cut).
    edges = pb._section_edges(lines, ".md")
    assert not any(lines[i].startswith("# not a heading") and r == 2 for i, r in edges.items())
    assert any(lines[i].startswith("## Topic") and r == 2 for i, r in edges.items())


def test_an_edit_changes_only_the_sections_around_it(small):
    text = code_file(120)
    before = {pb.section_text(text, s) for s in pb.split_sections(text, ".py")}
    edited = text.replace("    value_40_2 = a + b + 2\n", "    value_40_2 = a + b + 2\n    extra = 1\n" * 5)
    after = {pb.section_text(edited, s) for s in pb.split_sections(edited, ".py")}
    assert len(before) > 10 and len(after - before) <= 2


def test_real_size_constants_hold_a_big_file_in_about_a_dozen_kb_sections():
    text = code_file(900, 8)
    secs = pb.split_sections(text, ".py")
    sizes = [len(pb.section_text(text, s).encode()) for s in secs]
    assert len(text) > pb.CEILING_BYTES
    assert max(sizes) <= pb.SECTION_MAX and 6_000 <= sorted(sizes)[len(sizes) // 2] <= 20_000


# --- inventory -----------------------------------------------------------------------------

def test_inventory_admits_a_big_file_and_holds_one_over_the_section_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(pb, "CEILING_BYTES", 1000)
    monkeypatch.setattr(pb, "SECTION_MAX_BYTES", 5000)
    (tmp_path / "big.md").write_text(notes_file(5))
    (tmp_path / "huge.md").write_text(notes_file(40))
    (tmp_path / "secret.md").write_text(notes_file(5) + "the pass" + "word is hunter2" + "xyz9\n")
    files, held = pb.inventory([tmp_path])
    assert [p.name for p in files] == ["big.md"]
    why = dict((Path(p).name, w) for p, w in held)
    assert "even in sections" in why["huge.md"] and "split it into smaller .md files" in why["huge.md"]
    assert "card/password-like text" in why["secret.md"]


# --- connect end to end --------------------------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch, small):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "CEILING_BYTES", 3000)
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "gate", lambda *a, **k: pytest.fail("builtin writer needs no judge"))
    config = {"db": str(tmp_path / "answers.sqlite"), "registry": str(tmp_path / "registry.json")}
    registered, sent, drafted = set(), [], []

    def memory(req):
        sent.append(json.loads(json.dumps(req)))
        if req["action"] == "panel":
            return {"pointers": sorted(registered)}
        out = path_connect.connect(req, config)
        if out.get("status") == "registered":
            registered.add(req["pointer"])
        return out
    monkeypatch.setattr(pb, "memory", memory)
    real_excerpt = pb.excerpt

    def excerpt(p, text=None):
        drafted.append(Path(p).name)
        return real_excerpt(p, text)
    monkeypatch.setattr(pb, "excerpt", excerpt)
    root = tmp_path / "r"
    root.mkdir()
    (root / "small.py").write_text("def add(a, b):\n    return a + b\n")

    def go(*extra, refresh=False):
        sent.clear(); drafted.clear()
        base = ["prepare_bulk.py", "--pointer", "code", "--principal", "reader", "--writer", "builtin", "--no-shared"]
        base += ["--refresh"] if refresh else ["--root", str(root), "--ext", "py"]
        monkeypatch.setattr(sys, "argv", base + list(extra))
        code = pb.main()
        rep = json.loads((tmp_path / "cache" / "code-report.json").read_text())
        cache = json.loads((tmp_path / "cache" / "code.json").read_text())
        return code, rep, cache
    go.root, go.sent, go.drafted, go.config = root, sent, drafted, config
    return go


def test_big_file_connects_in_gated_sections_and_is_never_changed(run, capsys):
    big = run.root / "big.py"
    big.write_text(code_file())
    before = big.read_bytes()
    code, rep, cache = run()
    assert code == 0 and rep["connected"]
    assert str(big) in rep["approved"] and not any(str(big) == p for p, _ in rep["held"])
    secs = cache[str(big)]["sections"]
    assert len(secs) > 3 and all(s["pass"] and s["verdict"] == "QUOTED" for s in secs)
    assert rep["sections"][str(big)] == [s["lines"] for s in secs]
    assert cache[str(big)]["sha256"] == hashlib.sha256(before).hexdigest()
    confirm = [r for r in run.sent if r["action"] == "connect" and r.get("reviewed")][-1]
    lines = [s.get("lines") for s in confirm["sources"] if s["path"] == str(big)]
    assert lines == [s["lines"] for s in secs]
    # Each section's description comes from its own text.
    text = big.read_text()
    for s in secs:
        first_def = next(l for l in pb.file_lines(pb.section_text(text, s["lines"])) if l.startswith("def "))
        assert first_def.strip() in s["description"]
    assert big.read_bytes() == before
    assert "SECTIONS" in capsys.readouterr().out


def test_refresh_redrafts_only_the_sections_whose_text_changed(run, capsys):
    big = run.root / "big.py"
    big.write_text(code_file(120))
    run()
    first = len(run.drafted)
    big.write_text(big.read_text().replace("    value_60_2 = a + b + 2\n", "    value_60_2 = a + b + 2\n    more = 2\n"))
    code, rep, cache = run(refresh=True)
    assert code == 0 and rep["connected"]
    changed = {n for n in run.drafted if n.startswith("big.lines-")}
    assert first > 10 and 1 <= len(changed) <= 2
    assert "unchanged and already passing" in capsys.readouterr().out
    text = big.read_text()
    for s in cache[str(big)]["sections"]:  # every cached section matches the file's current lines
        assert s["sha256"] == hashlib.sha256(pb.section_text(text, s["lines"]).encode()).hexdigest()


def test_sections_are_split_across_parts_like_files(run):
    big = run.root / "big.py"
    big.write_text(code_file(200))
    code, rep, cache = run("--limit", "10")
    n = len(cache[str(big)]["sections"])
    assert code == 0 and n > 10
    assert sum(p["count"] for p in rep["parts"]) == n + 1  # every section plus small.py
    assert all(p["count"] <= 10 and p["connected"] for p in rep["parts"])


def test_a_section_changed_after_its_gate_is_never_sent(run, monkeypatch):
    big = run.root / "big.py"
    big.write_text(code_file())
    real = pb.connect_part

    def edit_then_connect(*a, **k):
        big.write_text(big.read_text().replace("value_0_1 = a + b + 1", "value_0_1 = a + b + 999"))
        return real(*a, **k)
    monkeypatch.setattr(pb, "connect_part", edit_then_connect)
    code, rep, cache = run()
    assert code == 1 and not rep["connected"]
    assert not any(r.get("reviewed") for r in run.sent if r["action"] == "connect")


def test_a_big_file_with_secret_text_is_still_held_whole(run):
    big = run.root / "big.py"
    big.write_text(code_file() + "TOKEN = 'pass" + "word: hunter2" + "xyz'\n")
    code, rep, cache = run()
    assert str(big) not in rep["approved"] and str(big) not in cache
    assert "card/password-like text" in dict(rep["held"])[str(big)]
    assert not any(str(big) in json.dumps(r) for r in run.sent)


def test_list_reads_each_sections_labels(run, capsys):
    big = run.root / "big.py"
    big.write_text(code_file())
    run()
    rows, _ = pb.list_cmd("code")
    assert any(r[4].startswith(str(big) + " (lines 1-") for r in rows)


def test_auto_heal_never_replays_a_section_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    import auto_heal
    calls = []

    def memory(req):
        calls.append(req["action"])
        return {"status": "ok", "recipe": {"pointer": "p", "dataset": "p", "principals": ["reader"],
                                           "structure": "flat-files",
                                           "sources": [{"path": "/x/big.md", "lines": [1, 40]}]}}
    assert auto_heal.reconnect_recipe("p", "reader", memory=memory) == "no-recipe"
    assert calls == ["recipe"]  # nothing was connected


def test_a_failed_section_is_an_exception_under_its_files_real_path(run, monkeypatch):
    import refresh_changed
    big = run.root / "big.py"
    big.write_text(code_file())
    real = pb.builtin_writer

    def no_draft_for_one(items):
        return {k: v for k, v in real(items).items() if "lines-1-" not in k}
    monkeypatch.setattr(pb, "builtin_writer", no_draft_for_one)
    code, rep, cache = run()
    assert code == 0 and str(big) in rep["approved"]
    failed = [(p, why) for p, why in rep["exceptions"]]
    assert failed and all(p == str(big) and why.startswith("lines 1-") for p, why in failed)
    assert not cache[str(big)]["sections"][0]["pass"] and cache[str(big)]["sections"][1]["pass"]
    assert str(big) in refresh_changed.known_files(rep)  # never taken for a new file


def test_max_files_counts_sections_before_any_paid_call(run, monkeypatch, capsys):
    big = run.root / "big.py"
    big.write_text(code_file())
    monkeypatch.setattr(pb, "builtin_writer", lambda items: pytest.fail("no draft past the guard"))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "code", "--principal", "reader", "--writer",
                                      "builtin", "--no-shared", "--root", str(run.root), "--ext", "py",
                                      "--max-files", "3"])
    assert pb.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "sections" in out and "--max-files 3" in out
    assert not [r for r in run.sent if r["action"] == "connect"]


def test_allow_held_never_sends_a_section_of_a_big_secret_file(run, capsys):
    big = run.root / "big.py"
    big.write_text(code_file() + "TOKEN = 'pass" + "word: hunter2" + "xyz'\n")
    code, rep, cache = run("--allow-held")
    assert str(big) not in rep["approved"] and str(big) not in cache
    why = dict(rep["held"])[str(big)]
    assert "never connected in sections" in why and "admitted" not in why
    out = capsys.readouterr().out
    assert "changed while this run read it" not in out
    assert not any(str(big) in json.dumps(r) for r in run.sent)


def test_blank_only_sections_are_never_drafted(run):
    big = run.root / "big.py"
    big.write_text(code_file(20) + "\n" * 9000 + code_file(20))
    code, rep, cache = run()
    blank = [x for x in cache[str(big)]["sections"] if x.get("verdict") == "blank"]
    assert code == 0 and blank and not any(x["pass"] for x in blank)
    assert not any("lines-{}-".format(x["lines"][0]) in n for x in blank for n in run.drafted)
