"""Table-of-contents search: plug-ins list a file's named parts, the TOC is cached by sha256, and the
search shortlists for free, lets the judge pick files from TOC pages, then reads located parts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import toc_search  # noqa: E402

PY = '''"""Lantern shop inventory helpers."""


def count_wicks(shelf):
    """Count the wicks left on a shelf."""
    return len(shelf)


class Restocker:
    def reorder(self, shelf):
        """Order more wicks when the shelf runs low."""
        if count_wicks(shelf) < 3:
            return place_order(shelf)
'''

SH = '''#!/bin/bash
# Nightly kettle descaler for the tea room.
set -e

descale() {
  echo descaling
}

function rinse {
  echo rinsing
}
'''

TS = '''// Garden bell widget.
export function ringBell(times: number) {
  return times;
}
const quiet = (x) => x;
export class Bell {}
'''

NOTE = '''Opening hours for the lantern shop.

# Weekdays
Open nine to five.

```
# not a heading inside a fence
```

## Holidays
Closed on the first snow day.
'''


def test_python_plugin_lists_functions_methods_docs_and_calls():
    toc = toc_search.build_toc("shop/stock.py", PY)
    assert toc["purpose"] == "Lantern shop inventory helpers."
    names = {p["name"]: p for p in toc["parts"]}
    assert names["count_wicks"]["start"] == 4 and names["count_wicks"]["end"] == 6
    assert names["count_wicks"]["doc"] == "Count the wicks left on a shelf."
    assert names["Restocker.reorder"]["start"] == 10
    assert {"count_wicks", "place_order"} <= set(names["Restocker.reorder"]["calls"])


def test_shell_plugin_lists_functions_and_top_comment():
    toc = toc_search.build_toc("bin/descale.sh", SH)
    assert toc["purpose"] == "Nightly kettle descaler for the tea room."
    assert [(p["name"], p["start"]) for p in toc["parts"]] == [("descale", 5), ("rinse", 9)]
    assert toc["parts"][0]["end"] == 8


def test_js_plugin_lists_functions_classes_and_arrows():
    toc = toc_search.build_toc("web/bell.ts", TS)
    assert toc["purpose"] == "Garden bell widget."
    assert [p["name"] for p in toc["parts"]] == ["ringBell", "quiet", "Bell"]


def test_note_headings_are_its_table_of_contents_and_fences_are_skipped():
    toc = toc_search.build_toc("notes/hours.md", NOTE)
    assert [p["name"] for p in toc["parts"]] == ["<intro>", "Weekdays", "Holidays"]
    assert toc["parts"][1]["end"] == 9
    assert toc["purpose"] == "Opening hours for the lantern shop."


def test_unparsable_python_falls_back_to_headings_not_an_error():
    toc = toc_search.build_toc("broken.py", "def (:\n# Notes\ntext\n")
    assert [p["name"] for p in toc["parts"]] == ["<intro>", "Notes"]


def test_cache_reuses_a_toc_at_the_same_bytes_and_rebuilds_when_they_change(tmp_path):
    reads = []

    def read(p):
        reads.append(p)
        return PY

    c = toc_search.TocCache(tmp_path / "toc.json")
    c.get("a.py", "sha1", read)
    c.save({"a.py"})
    c2 = toc_search.TocCache(tmp_path / "toc.json")
    assert c2.get("a.py", "sha1", read)["purpose"] == "Lantern shop inventory helpers."
    assert reads == ["a.py"]
    c2.get("a.py", "sha2", read)
    assert reads == ["a.py", "a.py"]


def test_called_by_links_a_definition_to_its_callers_but_not_common_names():
    tocs = {"stock.py": toc_search.build_toc("stock.py", PY),
            "report.py": {"purpose": "", "parts": [{"name": "summary", "start": 1, "end": 3, "doc": "", "calls": ["count_wicks", "len"]}]}}
    by = toc_search.called_by_index(tocs)
    assert by["stock.py"]["count_wicks"] == {"report.py"}
    assert "len" not in str(by)


def _fake_ask(texts):
    return {"read": texts.get, "has_secret": lambda t: "SECRET" in t,
            "query_terms": lambda q: set(q.lower().split()),
            "term_hits": lambda terms, text: sum(w in text.lower() for w in terms)}


def test_search_shortlists_picks_by_toc_and_reads_located_parts(monkeypatch):
    texts = {"/s/stock.py": PY, "/s/descale.sh": SH, "/s/hours.md": NOTE, "/s/vault.md": "SECRET wicks\n# a\nb\n# c\nd"}
    corpus = {p: ("ptr", {"sha256": p, "description": ""}) for p in texts}
    sent = []

    def ask_judge(state, qs, timeout=90):
        sent.append(state)
        return {"answers": {k: {"probabilities": {"LIKELY": 0.9 if "wicks" in v.lower() else 0.1}}
                            for k, v in state["items"].items()}}

    monkeypatch.setattr(toc_search.judges, "ask", ask_judge)
    files, chosen, trace = toc_search.run("when are wicks reordered", corpus, [], _fake_ask(texts))
    assert files[0] == "/s/stock.py"
    assert "/s/stock.py" in chosen
    assert all(isinstance(s, int) and isinstance(e, int) for _n, s, e in chosen["/s/stock.py"])
    assert not any("SECRET" in str(st) for st in sent)
    assert trace["calls"] >= 1


def test_word_search_hits_ride_along_and_best_line_window_is_read(monkeypatch):
    long_note = "# Log\n" + "\n".join(f"line {i}" for i in range(200)) + "\nthe descaler code is K42\n" + "\n".join("x" for _ in range(50))
    texts = {"/s/log.md": long_note + "\n# End\nbye\n", "/s/other.md": "# A\nnothing\n# B\nhere\n"}
    corpus = {p: ("ptr", {"sha256": p, "description": ""}) for p in texts}
    monkeypatch.setattr(toc_search.judges, "ask", lambda state, qs, timeout=90: {
        "answers": {k: {"probabilities": {"LIKELY": 0.5}} for k in state["items"]}})
    files, chosen, _ = toc_search.run("descaler code", corpus, [(0.5, "/s/log.md", "ptr")], _fake_ask(texts))
    assert "/s/log.md" in files
    names = [n for n, _s, _e in chosen["/s/log.md"]]
    assert "<best-matching lines>" in names
    s, e = next((s, e) for n, s, e in chosen["/s/log.md"] if n == "<best-matching lines>")
    assert s <= 203 <= e


def test_a_failed_judge_batch_raises_never_reads_as_none(monkeypatch):
    def boom(state, qs, timeout=90):
        return {"answers": {}}
    monkeypatch.setattr(toc_search.judges, "ask", boom)
    import pytest
    with pytest.raises(ValueError):
        toc_search.score_items("q", {"a": "x"}, "i", "p")


def test_a_big_files_page_names_every_part_that_did_not_fit():
    big = "\n".join(f"def part_{i:03d}():\n    return {i}\n" for i in range(160))
    toc = toc_search.build_toc("big.py", big)
    text = toc_search.page("/s/big.py", {}, toc, {}, rank=lambda x: 1 if x["name"] == "part_150" else 0)
    assert len(text) <= toc_search.BIG_PAGE_CHARS + 200
    assert text.index("part_150") < text.index("also:")  # the matching part leads, with its lines
    assert "part_159" in text or "part_100" in text  # later parts are named, not cut off
    assert "also:" in text


def test_a_small_files_page_is_unchanged():
    toc = toc_search.build_toc("a.py", PY)
    assert "also:" not in toc_search.page("/s/a.py", {}, toc, {})


def _scan(text):
    return "4000 0566 5566 5556" in text


def test_a_secret_shaped_comment_holds_its_section_not_the_file():
    text = ("def add(a, b):\n    return a + b\n\n" + "\n".join(f"# pad {i}" for i in range(10)) + "\n\n"
            "def scan(x):\n    # fake sample: 4000 0566 5566 5556\n    return x\n\n"
            + "\n".join(f"# more {i}" for i in range(10)) + "\n\ndef sub(a, b):\n    return a - b\n")
    clean, spans = toc_search.withhold_secret_sections(text, "m.py", _scan)
    assert clean is not None and not _scan(clean)
    assert "def add" in clean and "def sub" in clean
    assert "def scan" not in clean and toc_search.WITHHELD in clean
    assert len(clean.split("\n")) == len(text.split("\n"))  # line numbers kept
    assert spans and all(s <= 17 <= e for s, e in spans[:1])


def test_a_file_with_nothing_secret_is_returned_as_is():
    assert toc_search.withhold_secret_sections(PY, "a.py", _scan) == (PY, [])


def test_a_file_that_is_mostly_secret_or_unsectioned_is_held_whole():
    one_part = "Notes\n" + "\n".join(f"line {i}" for i in range(20)) + "\ncard 4000 0566 5566 5556\n"
    assert toc_search.withhold_secret_sections(one_part, "n.txt", _scan) == (None, [])
    secret_def = "def a():\n    return '4000 0566 5566 5556'\n"
    assert toc_search.withhold_secret_sections(secret_def, "a.py", _scan) == (None, [])


def test_a_secret_split_over_two_sections_is_held_whole():
    split = "def a():\n    return 'HALF1'\n\ndef b():\n    return 'HALF2'\n"
    scan = lambda t: "HALF1" in t and "HALF2" in t  # noqa: E731
    assert toc_search.withhold_secret_sections(split, "a.py", scan) == (None, [])


def test_a_secret_beside_a_section_edge_is_withheld_with_its_margin():
    pad = "\n".join(f"# pad {i}" for i in range(30))
    text = f"def a():\n    return 1\n{pad}\ndef b():\n    key = 'AAAA'\n    more = 'tail'\n\ndef c():\n    return 2\n{pad}\n"
    scan = lambda t: "AAAA" in t  # noqa: E731
    clean, spans = toc_search.withhold_secret_sections(text, "a.py", scan)
    assert clean is not None and "AAAA" not in clean and "tail" not in clean
    assert "return 1" in clean and "# pad 29" in clean  # only the neighbourhood goes, never the whole file
