#!/usr/bin/env python3
"""The zoom on a real file index, end to end through ask.lookup / ask.main: folder rows are built by the updater
and an ask reads only the rows of the folders it keeps; a failed zoom reads the word search's list and names
why; a claim and --json ride the same zoom; a file read as parts sends only its parts. Made-up files, a stub
judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_zoom_index.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_index_read_path import PRINCIPAL, Rig, ask, ask_it, build, flag, sync  # noqa: E402
from file_index import FileIndex  # noqa: E402
import zoom  # noqa: E402

pytestmark = pytest.mark.real_toc

Q = "where is the frobnic lantern stored"
LANTERN = '''"""Where the frobnic lantern is stored between shows."""


def lantern_shelf():
    """The frobnic lantern is stored on the top shelf of the shed."""
    return "top shelf"


def lantern_wick():
    return "cotton"
'''


def _areas(tmp_path, monkeypatch, n_areas=12, answer=("area07", "lantern-frobnic.py", LANTERN)):
    """build()'s three sets, plus p0 split into n_areas folders of 6 notes: more folders than the zoom keeps."""
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    cdir = ask.prepare_bulk.CACHE_DIR
    cache = json.loads((cdir / "p0.json").read_text())
    for k in range(n_areas):
        d = notes / "p0" / f"area{k:02d}"
        d.mkdir()
        for i in range(6):
            f = d / f"item-{k:02d}-{i}.md"
            f.write_text(f"# Item {k} {i}\nplain stock list {k} {i}\n")
            cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True, "description": f.name, "question": ""}
    folder, fname, text = answer
    f = notes / "p0" / folder / fname
    f.write_text(text)
    cache[str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True, "description": fname, "question": ""}
    (cdir / "p0.json").write_text(json.dumps(cache))
    return notes, names, sdir, f


def _trace(sdir):
    return json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])


def _kept(folders: dict) -> list:
    return [f for f, *_ in folders["top"][:zoom.KEEP_FOLDERS]] + list(folders.get("net_added") or [])


def test_the_updater_stores_folder_rows_and_an_ask_reads_only_the_kept_folders(tmp_path, monkeypatch, capsys):
    notes, names, sdir, answer = _areas(tmp_path, monkeypatch)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    idx = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    rows = idx.db.execute("SELECT folder, n FROM folders WHERE pointer='p0'").fetchall()
    assert len(rows) >= 12 and dict(rows)[str(answer.parent)] == 7
    assert idx.db.execute("SELECT folder FROM fts_map WHERE path=?", (str(answer),)).fetchone()[0] == str(answer.parent)
    idx.close()
    flag(monkeypatch, True)
    asked = []
    real = zoom.IndexStore.rows
    monkeypatch.setattr(zoom.IndexStore, "rows", lambda self, paths: (asked.append(list(paths)), real(self, paths))[1])
    rc, out = ask_it(Q, sdir, capsys)
    assert rc == 0 and str(answer) in out
    toc = _trace(sdir)["stages"]["toc"]
    assert toc["folders"]["listed"] > zoom.KEEP_FOLDERS and toc["folders"]["top"]  # Jev was asked about folders
    kept = _kept(toc["folders"])
    assert str(answer.parent) in kept
    first = asked[0]  # the zoom's one read of TOC rows: only files of kept folders (and the word search's hits)
    hits = {r["path"] for r in _trace(sdir)["stages"]["word_search"]["top"]}
    assert first and all(str(Path(p).parent) in kept or p in hits for p in first), first
    assert len(first) <= zoom.POOL_CAP


def test_a_folder_whose_files_the_word_search_ranks_is_kept_though_jev_ranks_it_low(tmp_path, monkeypatch):
    texts = {f"/c/f{k:02d}/n{i}.md": f"# Note\ntext {k}\n" for k in range(12) for i in range(5)}
    texts.update({f"/c/hooks/h{i}.md": "# h\nh\n" for i in range(5)})
    decoys = [f"/c/f{k:02d}" for k in range(zoom.KEEP_FOLDERS)]
    monkeypatch.setattr(zoom.toc_search, "score_items", lambda q, items, ins, purpose: {
        i: (0.9 if i in decoys else 0.5 if i.startswith("/c/f") else 0.4 if i == "/c/hooks" else 0.1) for i in items})
    store = zoom.MemoryStore({p: ("shop", {"sha256": "s", "description": ""}) for p in texts}, texts.get)
    hooks = {"read": texts.get, "has_secret": lambda t: False, "query_terms": lambda q: ["budget"],
             "term_hits": lambda terms, t: 0}
    _f, _c, plain = zoom.run("q", store, [], hooks)
    assert "/c/hooks" not in _kept(plain["folders"])  # Jev alone leaves it out
    _f, _c, fused = zoom.run("q", store, [], hooks, word={"/c/hooks/h2.md": 6.0, "/c/f01/n1.md": 1.0})
    assert "/c/hooks" in _kept(fused["folders"])  # its word score lifts it past Jev-only folders
    top = {f: (s, j, w) for f, s, j, w in fused["folders"]["top"]}
    assert top["/c/hooks"] == (round(0.5 * 0.4 + 0.5 * 1.0, 3), 0.4, 1.0)


@pytest.mark.parametrize("index_on", [True, False])
def test_a_failed_zoom_reads_the_word_search_list_and_names_why(tmp_path, monkeypatch, capsys, index_on):
    notes, names, sdir, answer = _areas(tmp_path, monkeypatch)
    rig = Rig(monkeypatch, notes, names)
    sync(sdir)
    flag(monkeypatch, index_on)
    read = []
    real_confirm = rig.confirm
    monkeypatch.setattr(ask, "confirm", lambda q, ps: (read.append(list(ps)), real_confirm(q, ps))[1])

    def broken(*_a, **_k):
        raise RuntimeError("judge down")
    monkeypatch.setattr(zoom.toc_search, "score_items", broken)
    rc, out = ask_it(Q, sdir, capsys)
    why = "zoom failed: RuntimeError: judge down; read list from word search only"
    assert out.startswith("OUTCOME: found") and why in out.splitlines()[0]
    assert str(answer) in out
    tr = _trace(sdir)["stages"]
    assert tr["toc"]["note"] == why and tr["toc"]["read_list"] == "word search only"
    words = [r["path"] for r in tr["word_search"]["top"]][:ask.WORD_HITS]
    assert read == [words] and str(answer) in words  # the fallback reads exactly its list


def test_a_claim_is_read_through_the_zoom_with_no_routing_call(tmp_path, monkeypatch, capsys):
    notes, names, sdir, answer = _areas(tmp_path, monkeypatch)
    Rig(monkeypatch, notes, names)  # its memory() fails any navigate: a claim asks routing nothing
    claim = "the frobnic lantern is stored on the top shelf of the shed"
    seen = {}

    def listwise(question, paths):
        seen["pool"] = list(paths)
        ask._STAGE["claim_files"] = {str(answer): {"verdict": "supported", "prob": 0.97,
                                                   "line": "The frobnic lantern is stored on the top shelf of the shed.",
                                                   "line_no": 5}}
        return str(answer), 0.97
    monkeypatch.setattr(ask, "judge_listwise", listwise)
    monkeypatch.setattr(ask, "claim_cache_put", lambda *a, **k: None)
    ask._CLAIM["text"] = claim
    try:
        rc, out = ask_it(claim, sdir, capsys)
    finally:
        ask._CLAIM["text"] = None
    assert out.splitlines()[0].startswith("TRUE") and str(answer) in out, out
    st = _trace(sdir)["stages"]
    assert st["toc"]["pick"]["pool"] >= 1 and "error" not in st["toc"]
    assert st["routing_fallback"] == [] and str(answer) in seen["pool"]


def test_json_lists_the_part_the_content_check_scored_and_sends_only_parts(tmp_path, monkeypatch, capsys):
    notes, names, sdir, answer = _areas(tmp_path, monkeypatch)
    real_confirm = ask.confirm
    Rig(monkeypatch, notes, names)
    monkeypatch.setattr(ask, "confirm", real_confirm)  # the real content check (its provider call is stubbed below)
    sent = []

    def navigation(payload):
        sent.append(payload)
        leaves = [n for n in payload["catalog"]["nodes"] if n["id"] != "root"]
        return {"status": "candidates", "candidates": [
            {"sourceId": n["sourceId"], "score": 0.92 if "top shelf" in n["description"] and n["description"].startswith("lantern_shelf")
             else 0.1} for n in leaves]}, None
    monkeypatch.setattr(ask, "run_navigation", navigation)
    sync(sdir)
    flag(monkeypatch, True)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--json", "--principal", PRINCIPAL, Q])
    capsys.readouterr()
    rc = ask.main()
    got = json.loads(capsys.readouterr().out)
    assert rc == 0 and got["outcome"] == "found", got
    row = next(f for f in got["files"] if f["path"] == str(answer))
    assert row["location"] == {"unit": "lines", "start": 4, "end": 6, "part": "lantern_shelf"}
    mine = next(p for p in sent if any("lantern-frobnic.py" in n.get("description", "") for n in p["catalog"]["nodes"]))
    leaves = [n["description"] for n in mine["catalog"]["nodes"] if n["id"] != "root"]
    # parts only (each named with its lines) and the outline: the whole file is not sent beside them
    assert leaves and all(d.startswith(("lantern_", "<best-matching lines>", "Outline of lantern-frobnic.py")) for d in leaves), leaves
    assert any(d.startswith("Outline of lantern-frobnic.py") for d in leaves)
