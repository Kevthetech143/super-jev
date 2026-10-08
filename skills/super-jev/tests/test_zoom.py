"""The zoom read list: folders, then files, then parts, one yes/no per item, word-search hits kept at every
level; a code file's outline rides to the content check as one more passage while its bytes are unchanged;
named reasons when there is no span or the outline is left out. Made-up files only; the judge is a stub.

    python3 -m pytest skills/super-jev/tests/test_zoom.py -q
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask  # noqa: E402
import toc_search  # noqa: E402
import zoom  # noqa: E402

BELL = '''"""Ring the shop bell when a candle order is ready."""


def ring_bell(order):
    """Ring once per finished order."""
    return chime(order)


def chime(order):
    return order
'''
CALLER = '''def finish(order):
    return ring_bell(order)
'''


def _hooks(texts, mentions=None):
    return {"read": texts.get, "has_secret": lambda t: "SECRET" in t,
            "query_terms": lambda q: set(q.lower().replace("?", "").split()),
            "term_hits": lambda terms, text: sum(w in text.lower() for w in terms),
            **({"mentions": mentions} if mentions else {})}


def _corpus(texts, ptr="shop"):
    return {p: (ptr, {"sha256": "sha-" + p, "description": ""}) for p in texts}


def _store(texts, ptr="shop"):
    return zoom.MemoryStore(_corpus(texts, ptr), texts.get)


def _judge(rule):
    """A stub judge: LIKELY when rule(item text) is true. Records every item sent."""
    sent = []

    def ask_judge(state, qs, timeout=90):
        sent.append(state)
        return {"answers": {k: {"probabilities": {"LIKELY": 0.9 if rule(v) else 0.1}} for k, v in state["items"].items()}}
    return ask_judge, sent


def test_small_folders_list_under_their_parent_but_never_above_the_sets_top():
    corpus = _corpus({"/c/shop/a.md": "", "/c/shop/b.md": "", "/c/shop/x/one.md": "",
                      **{f"/c/shop/big/n{i}.md": "" for i in range(6)}})
    unit = zoom.folder_units({p: ptr for p, (ptr, _e) in corpus.items()})
    assert unit["/c/shop/x/one.md"] == "/c/shop"     # a folder of one file rolls up
    assert unit["/c/shop/big/n0.md"] == "/c/shop/big"  # a folder with enough files stays
    assert unit["/c/shop/a.md"] == "/c/shop"          # the set's top folder is the ceiling


def test_folders_are_asked_first_and_an_unpicked_folder_is_never_read(monkeypatch):
    texts = {f"/c/f{k}/n{i}.md": f"# Note\nplain text {k}\n" for k in range(12) for i in range(5)}
    texts["/c/bells/ring.py"] = BELL
    texts.update({f"/c/bells/n{i}.md": "# Bells\nbell notes\n" for i in range(4)})
    judge, sent = _judge(lambda item: "ring" in item)
    monkeypatch.setattr(toc_search.judges, "ask", judge)
    files, _chosen, trace = zoom.run("where is the shop bell rung", _store(texts), [], _hooks(texts))
    assert trace["folders"]["listed"] == 13 and trace["folders"]["calls"] >= 1
    assert trace["folders"]["top"][0][0] == "/c/bells"
    assert files[0] == "/c/bells/ring.py"
    first = sent[0]["items"]  # level 1 sends folder pages, never file text
    assert all("/ (" in v and "plain text" not in v for v in first.values())


def test_a_word_search_hit_keeps_its_folder_and_its_file(monkeypatch):
    texts = {f"/c/f{k}/n{i}.md": f"# Note\ntext {k}\n" for k in range(12) for i in range(5)}
    judge, _ = _judge(lambda item: False)
    monkeypatch.setattr(toc_search.judges, "ask", judge)
    files, _c, trace = zoom.run("anything", _store(texts), [(0.8, "/c/f7/n3.md", "shop")], _hooks(texts))
    assert "/c/f7" in trace["folders"]["net_added"]
    assert "/c/f7/n3.md" in files


def test_few_folders_ask_jev_nothing_at_level_one(monkeypatch):
    texts = {"/c/shop/ring.py": BELL, "/c/shop/notes.md": "# Bell\nrung at noon\n"}
    judge, sent = _judge(lambda item: "ring" in item)
    monkeypatch.setattr(toc_search.judges, "ask", judge)
    _f, _c, trace = zoom.run("when is the bell rung", _store(texts), [], _hooks(texts))
    assert trace["folders"] == {"listed": 1, "calls": 0}
    assert not any("/ (" in v for st in sent for v in st["items"].values())


def test_a_code_file_gets_an_outline_with_a_source_line_for_every_fact(monkeypatch):
    texts = {"/c/shop/bell.py": BELL, "/c/shop/till.py": CALLER, "/c/shop/hours.md": "# Hours\nopen at nine\n"}
    judge, _ = _judge(lambda item: True)
    monkeypatch.setattr(toc_search.judges, "ask", judge)
    named = [("/c/shop/hours.md", 7, "the closing job runs bell.py at five"),
             ("/c/shop/keys.md", 3, "bell.py key SECRET-123")]
    _f, _c, trace = zoom.run("what rings the bell", _store(texts), [], _hooks(texts, lambda p: named))
    out = trace["outlines"]["/c/shop/bell.py"]
    assert out["sha256"] == "sha-/c/shop/bell.py"
    text = out["text"]
    assert "purpose (/c/shop/bell.py:1)" in text
    assert "part ring_bell (/c/shop/bell.py:4-6)" in text
    assert "ring_bell is called at /c/shop/till.py:2" in text
    assert "named at /c/shop/hours.md:7" in text
    assert "SECRET" not in text  # a fact that scans as a secret is dropped, never sent
    assert "/c/shop/hours.md" not in trace["outlines"]  # notes get no outline


def _stage_outline(path: Path, sha: str):
    ask._STAGE.clear()
    ask._STAGE["outlines"] = {str(path): {"sha256": sha, "text": "Outline of bell.py\nnamed at /c/x.md:3: runs bell.py"}}


def test_the_outline_rides_to_the_content_check_while_the_bytes_match(tmp_path):
    f = tmp_path / "bell.py"
    f.write_text(BELL)
    _stage_outline(f, hashlib.sha256(f.read_bytes()).hexdigest())
    done, ctx = ask.confirm_start("what rings the bell", str(f))
    assert done is None and ctx["outline_i"] is not None
    leaves = [n for n in ctx["payload"]["catalog"]["nodes"] if n["id"] != "root"]
    assert any(n["description"].startswith("Outline of bell.py") for n in leaves)


def test_a_changed_file_drops_its_outline_and_says_why(tmp_path):
    f = tmp_path / "bell.py"
    f.write_text(BELL)
    _stage_outline(f, "an-older-sha")
    _done, ctx = ask.confirm_start("what rings the bell", str(f))
    assert ctx["outline_i"] is None
    assert ask._STAGE["outline_ignored"][str(f)] == "outline ignored: file changed since outline"


def test_an_outline_that_scores_best_lists_the_file_with_a_named_no_span(tmp_path, capsys):
    f = tmp_path / "bell.py"
    f.write_text(BELL)
    _stage_outline(f, hashlib.sha256(f.read_bytes()).hexdigest())
    _done, ctx = ask.confirm_start("what rings the bell", str(f))
    oi = ctx["outline_i"]
    body = {"status": "candidates", "candidates": [{"sourceId": str(oi), "score": 0.95}, {"sourceId": "0", "score": 0.4}]}
    score, *_ = ask.confirm_finish("what rings the bell", ctx, body, None)
    assert score == 0.95
    row = ask.file_row(0.95, str(f), "shop", "confirmed")
    assert "location" not in row and row["no_span"] == "no span: the outline passage scored best"
    ask.show_file(row)
    out = capsys.readouterr().out.splitlines()
    assert f"{f}  [shop]" in out[0]  # the path alone: no span is made up
    assert out[1].strip() == "no span: the outline passage scored best"


def test_the_outline_never_wins_a_tie_against_a_passage_of_the_file():
    chunks, parts = ["intro\n" * 30], [("ring_bell", 4, 6)]
    c = [{"sourceId": "2", "score": 0.9}, {"sourceId": "1", "score": 0.9}]
    assert ask.tightest_near_top(c[0], c, chunks + ["p", "outline"], parts, 1, outline_i=2)["sourceId"] == "1"


def test_kept_folders_take_turns_so_a_big_folder_cannot_crowd_out_a_small_one(monkeypatch):
    texts = {f"/c/log/bell-{i}.md": "# bell bell bell\nbell\n" for i in range(zoom.POOL_CAP + 20)}
    texts.update({f"/c/code/n{i}.md": "# misc\nnothing\n" for i in range(4)})
    texts["/c/code/ring.py"] = BELL
    judge, sent = _judge(lambda item: "ring" in item)
    monkeypatch.setattr(toc_search.judges, "ask", judge)
    files, _c, trace = zoom.run("which bell", _store(texts), [], _hooks(texts))
    assert trace["pick"]["pool"] == zoom.POOL_CAP
    assert files[0] == "/c/code/ring.py"
