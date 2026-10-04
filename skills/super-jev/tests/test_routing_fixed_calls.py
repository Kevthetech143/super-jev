#!/usr/bin/env python3
"""S4: Jev calls on a plain question ask do not grow with the number of connected sets. Routing is never a per-set
Jev call: every set is found through local rows (its cache, its split parent's cache, or rows built from its reviewed
sources). Only a set with no local rows at all is asked, in one batched call, capped and named in the trace.
Made-up state and a stub judge that counts calls; no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_routing_fixed_calls.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_fixed_calls", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
import toc_search  # noqa: E402

pytestmark = pytest.mark.real_toc

PRINCIPAL = "me"
QUESTION = "when does the zorblax quenth shipment arrive"
ANSWER = "zorblax quenth shipment arrives on the fourth of May"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Rig:
    """Engine stub (panel, sources, navigate) and a judge stub; every Jev-shaped call is counted."""
    def __init__(self, monkeypatch, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.root = tmp_path
        self.cache = tmp_path / "prepare-cache"
        self.cache.mkdir()
        self.state = tmp_path / "state"
        self.sdir = self.state / PRINCIPAL
        self.panel, self.sources = [], {}
        self.navigate_many, self.navigate, self.judge = [], [], 0
        monkeypatch.setenv("SUPERJEV_STATE_DIR", str(self.state))
        monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")
        monkeypatch.setenv("SUPERJEV_INDEX", "0")
        monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", self.cache)
        monkeypatch.setattr(ask, "memory", self.memory)
        monkeypatch.setattr(ask, "confirm", self.confirm)
        monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
        monkeypatch.setattr(toc_search, "score_items", self.score)
        monkeypatch.setattr(ask, "spawn_index_updater", lambda principal: None)
        monkeypatch.setattr(ask, "engine_visible", lambda principal: {r["pointer"] for r in self.panel})

    def note(self, folder, name, text):
        f = self.root / folder / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f

    def cached_set(self, ptr, files):
        self.cache.joinpath(f"{ptr}.json").write_text(json.dumps(
            {str(f): {"sha256": sha(f), "pass": True, "description": f.name, "question": ""} for f in files}))
        self.panel.append({"pointer": ptr, "snapshotStatus": "ready", "generation": f"g-{ptr}"})

    def sources_set(self, ptr, files, view=False):
        self.sources[ptr] = [{"originalPath": str(f), "contentSHA": sha(f), "path": str(f), "description": f.name} for f in files]
        row = {"pointer": ptr, "snapshotStatus": "ready", "generation": f"g-{ptr}"}
        if view:
            row["viewOriginals"] = [str(f) for f in files]
        self.panel.append(row)

    def memory(self, req):
        a = req["action"]
        if a == "cached":
            return {"status": "cache-miss", "checked": []}
        if a == "panel":
            return {"pointers": list(self.panel)}
        if a == "sources":
            rows = self.sources.get(req["pointer"], [])
            return {"status": "ok", "sources": rows[req["offset"]:req["offset"] + req["limit"]]}
        if a == "navigate-many":
            self.navigate_many.append(list(req["pointers"]))
            return {"status": "ok", "results": {p: {"status": "no-candidates"} for p in req["pointers"]}}
        if a == "navigate":
            self.navigate.append(req["pointer"])
            return {"status": "no-candidates"}
        raise AssertionError(req)

    def score(self, question, items, instruction, purpose):
        self.judge += 1
        terms = set(ask.query_terms(question))
        return {i: min(0.99, sum(w in t.lower() for w in terms) / max(1, len(terms))) for i, t in items.items()}

    def confirm(self, question, paths):
        self.judge += 1
        terms = set(ask.query_terms(question))
        out = {p: min(0.95, sum(w in Path(p).read_text().lower() for w in terms) / max(1, len(terms))) for p in paths}
        return out, set(), None, {}

    def jev_calls(self):
        return len(self.navigate_many) + len(self.navigate) + self.judge

    def ask(self, capsys, question=QUESTION):
        capsys.readouterr()
        ask.lookup(question, PRINCIPAL, self.sdir)
        return capsys.readouterr().out

    def trace(self):
        return json.loads((self.sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]


def many_sets(rig, n):
    """n connected sets: a mix of plain sets and split parts (no cache of their own), one answer file in one plain set."""
    big = [rig.note("big", f"b{i}.md", f"# b{i}\ncedar lamp ledger {i} river table\n") for i in range(3)]
    rig.cached_set("big", big)
    for k in range(2, n):  # n-1 more sets: split parts of `big`, never a cache of their own
        rig.panel.append({"pointer": f"big-{k}", "snapshotStatus": "ready", "generation": f"g-{k}"})
    ans = rig.note("plain", "zorblax.md", f"# zorblax\n{ANSWER}.\n")
    rig.cached_set("plain", [ans])
    return ans


def test_calls_per_ask_do_not_grow_with_sets(tmp_path, monkeypatch, capsys):
    calls = {}
    for sets in (10, 100):
        rig = Rig(monkeypatch, tmp_path / str(sets))
        many_sets(rig, sets)
        assert "zorblax.md" in rig.ask(capsys)
        assert rig.navigate_many == [] and rig.navigate == []  # routing asked Jev nothing
        calls[sets] = rig.jev_calls()
    assert calls[10] == calls[100] and 0 < calls[10] <= 4


def test_split_part_set_still_yields_its_file(tmp_path, monkeypatch, capsys):
    rig = Rig(monkeypatch, tmp_path)
    ans = rig.note("big", "zorblax.md", f"# zorblax\n{ANSWER}.\n")
    other = rig.note("big", "b0.md", "# b0\ncedar lamp ledger\n")
    rig.cached_set("parent", [other, ans])  # one cache lists every part's files
    rig.panel.append({"pointer": "parent-2", "snapshotStatus": "ready", "generation": "g2"})
    out = rig.ask(capsys)
    assert "zorblax.md" in out
    assert rig.navigate_many == [] and rig.navigate == []
    assert rig.trace()["routing_fallback"] == []


def test_reviewed_view_set_still_yields_its_file(tmp_path, monkeypatch, capsys):
    rig = Rig(monkeypatch, tmp_path)
    view = rig.note("views", "0.txt", f"# zorblax\n{ANSWER}.\n")
    rig.sources_set("brain-reviewed", [view], view=True)
    # a leftover raw bulk cache of the same name must never be read instead of the reviewed view
    raw = rig.note("raw", "private.md", "# raw\nPRIVATE_CANARY zorblax\n")
    rig.cache.joinpath("brain-reviewed.json").write_text(json.dumps({str(raw): {"sha256": sha(raw), "pass": True}}))
    rig.cached_set("plain", [rig.note("plain", "n.md", "# n\ncedar lamp ledger\n")])
    out = rig.ask(capsys)
    assert "0.txt" in out and "PRIVATE_CANARY" not in out and "private.md" not in out
    assert rig.navigate_many == [] and rig.navigate == []


def test_manual_note_set_still_yields_its_file(tmp_path, monkeypatch, capsys):
    rig = Rig(monkeypatch, tmp_path)
    note = rig.note("state/manual", f"{PRINCIPAL}-manual-0123456789.md", f"# zorblax\n{ANSWER}.\n")
    rig.sources_set(f"{PRINCIPAL}-manual-0123456789", [note])
    rig.cached_set("plain", [rig.note("plain", "n.md", "# n\ncedar lamp ledger\n")])
    out = rig.ask(capsys)
    assert note.name in out
    assert rig.navigate_many == [] and rig.navigate == []


def test_rows_are_kept_per_generation(tmp_path, monkeypatch, capsys):
    rig = Rig(monkeypatch, tmp_path)
    rig.sources_set("rv", [rig.note("views", "0.txt", f"# zorblax\n{ANSWER}.\n")], view=True)
    rig.ask(capsys)
    saved = json.loads((rig.sdir / ask.SET_ROWS_FILE).read_text())
    assert saved["rv"]["generation"] == "g-rv" and len(saved["rv"]["rows"]) == 1
    rig.sources = {}  # the engine is not asked again for an unchanged generation
    assert "0.txt" in rig.ask(capsys)


@pytest.mark.parametrize("sets", [1, 30])
def test_fallback_is_one_batched_call_and_named(tmp_path, monkeypatch, capsys, sets):
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "1")
    rig = Rig(monkeypatch, tmp_path)
    rig.cached_set("plain", [rig.note("plain", "zorblax.md", f"# zorblax\n{ANSWER}.\n")])
    for i in range(sets):  # sets with no cache, no parent and no reviewed sources: nothing local to find them by
        rig.panel.append({"pointer": f"bare{i}", "snapshotStatus": "ready", "generation": f"g{i}"})
    out = rig.ask(capsys)
    assert "zorblax.md" in out
    assert len(rig.navigate_many) == 1 and rig.navigate == []
    assert len(rig.navigate_many[0]) == min(sets, ask.NAV_FALLBACK_MAX)
    fb = rig.trace()["routing_fallback"]
    assert fb[0] == "bare0: no local rows" and len(fb) == min(sets, ask.NAV_FALLBACK_MAX) + (sets > ask.NAV_FALLBACK_MAX)
    if sets > ask.NAV_FALLBACK_MAX:
        assert fb[-1].startswith("20 more sets") and "over the cap" in fb[-1]


def test_fallback_is_capped_even_with_batching_off(tmp_path, monkeypatch, capsys):
    rig = Rig(monkeypatch, tmp_path)  # the suite default: SUPERJEV_BATCH_JEV=0
    rig.cached_set("plain", [rig.note("plain", "zorblax.md", f"# zorblax\n{ANSWER}.\n")])
    for i in range(50):
        rig.panel.append({"pointer": f"bare{i}", "snapshotStatus": "ready", "generation": f"g{i}"})
    assert "zorblax.md" in rig.ask(capsys)
    assert len(rig.navigate) == ask.NAV_FALLBACK_MAX and rig.navigate_many == []
