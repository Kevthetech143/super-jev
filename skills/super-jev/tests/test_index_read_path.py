#!/usr/bin/env python3
"""The flag-gated index read path (SUPERJEV_INDEX=1): the ask reads its candidate list and pointer status from
the file index and sha-checks only the files it serves. Made-up files and a stub judge, no network, no spend.

    python3 -m pytest skills/super-jev/tests/test_index_read_path.py -q
    python3 skills/super-jev/tests/test_index_read_path.py        # prints the 1x vs 10x ask time table
"""
import hashlib
import importlib.util
import json
import random
import re
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_index_read", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
import toc_search  # noqa: E402

pytestmark = pytest.mark.real_toc

PRINCIPAL = "me"
VOCAB = [f"{a}{b}" for a in ("river", "stone", "cedar", "linen", "ember", "copper", "willow", "harbor", "velvet", "maple")
         for b in ("table", "lamp", "route", "ledger", "garden", "window", "basket", "ticket", "shelf", "clock")]
PLANTED = [  # (question, answer file name, the words only that file holds)
    ("when does the zorblax quenth shipment arrive", "zorblax.md", "zorblax quenth shipment arrives on the fourth of May"),
    ("who maintains the plimbus frontier gate", "plimbus.md", "plimbus frontier gate is maintained by Harriet"),
    ("how often is the wumpet saltern drained", "wumpet.md", "wumpet saltern is drained every ninth week"),
    ("where is the glorpen archive kept", "glorpen.md", "glorpen archive is kept in the cellar vault"),
    ("what colour is the snarfle kiosk", "snarfle.md", "snarfle kiosk is painted a pale teal colour"),
]


def build(tmp_path, monkeypatch, n_files, n_ptr=3, tag="c"):
    """A made-up connected corpus of n_files noise notes in n_ptr pointers + the 5 planted answer files."""
    root = tmp_path / tag
    cdir, notes = root / "prepare-cache", root / "notes"
    cdir.mkdir(parents=True)
    state = root / "state"
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")  # an ask never saves an answer here
    monkeypatch.setattr(ask.prepare_bulk, "CACHE_DIR", cdir)
    rng = random.Random(7)
    names = []
    for i in range(n_ptr):
        (notes / f"p{i}").mkdir(parents=True)
        names.append(f"p{i}")
    caches = {n: {} for n in names}

    def add(i, fname, text):
        f = notes / names[i % n_ptr] / fname
        f.write_text(text)
        caches[names[i % n_ptr]][str(f)] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "pass": True,
                                           "description": fname, "question": ""}
    for i in range(n_files):
        add(i, f"note-{i:05d}.md", f"# Note {i}\n" + "\n".join(" ".join(rng.choice(VOCAB) for _ in range(9)) for _ in range(12)) + "\n")
    for j, (_q, fname, line) in enumerate(PLANTED):
        add(j, fname, f"# {fname[:-3]}\n{line}.\n" + " ".join(rng.choice(VOCAB) for _ in range(30)) + "\n")
    for n in names:
        (cdir / f"{n}.json").write_text(json.dumps(caches[n]))
        (cdir / f"{n}-report.json").write_text(json.dumps({"pointer": n, "principals": [PRINCIPAL], "roots": [str(notes / n)]}))
    return notes, names, state / PRINCIPAL


class Rig:
    """Stubs the engine panel/cache and the judge; counts every corpus file opened."""
    def __init__(self, monkeypatch, notes, names):
        self.opened, self.panel_calls = set(), 0
        self.names, self.notes = names, notes
        monkeypatch.setattr(ask, "memory", self.memory)
        monkeypatch.setattr(ask, "confirm", self.confirm)
        monkeypatch.setattr(ask, "run_gate", lambda claim, path, passage=None: ("CLEAN", 0.93))
        monkeypatch.setattr(toc_search, "score_items", self.score)
        real = Path.read_bytes
        rig = self

        def counting(p):
            if str(p).startswith(str(rig.notes)):
                rig.opened.add(str(p))
            return real(p)
        monkeypatch.setattr(Path, "read_bytes", counting)
        monkeypatch.setattr(ask, "spawn_index_updater", lambda principal: None)

    def memory(self, req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            self.panel_calls += 1
            return {"pointers": [{"pointer": n, "snapshotStatus": "ready", "generation": 1} for n in self.names]}
        raise AssertionError(req)

    @staticmethod
    def score(question, items, instruction, purpose):
        terms = set(ask.query_terms(question))
        return {i: min(0.99, sum(w in t.lower() for w in terms) / max(1, len(terms))) for i, t in items.items()}

    def confirm(self, question, paths):
        terms = set(ask.query_terms(question))
        out = {}
        for p in paths:
            text = Path(p).read_bytes().decode().lower()
            out[p] = min(0.95, sum(w in text for w in terms) / max(1, len(terms)))
        return out, set(), None, {}


def ask_it(question, sdir, capsys):
    capsys.readouterr()
    rc = ask.lookup(question, PRINCIPAL, sdir)
    return rc, capsys.readouterr().out


def top5(out):
    return re.findall(r"/(?:note-\d+|zorblax|plimbus|wumpet|glorpen|snarfle)\.md", out)[:5]


def flag(monkeypatch, on):
    monkeypatch.setenv("SUPERJEV_INDEX", "1" if on else "0")


def sync(sdir):
    assert ask.index_sync(PRINCIPAL, sdir) == 0


def test_flag_off_unset_and_zero_are_byte_identical(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    Rig(monkeypatch, notes, names)
    outs = []
    for setting in (None, "0"):
        (monkeypatch.delenv("SUPERJEV_INDEX", raising=False) if setting is None else monkeypatch.setenv("SUPERJEV_INDEX", setting))
        monkeypatch.setattr(ask, "_engine_target", lambda: None)  # no live config is read
        outs.append(ask_it(PLANTED[0][0], sdir, capsys))
    assert outs[0] == outs[1] and "zorblax.md" in outs[0][1]
    assert not (sdir / "index.sqlite").exists()  # flag off never creates or reads an index


def test_flag_on_same_top5_and_few_files_hashed_at_1x_and_10x(tmp_path, monkeypatch, capsys):
    hashed, tops = {}, {}
    for tag, n in (("1x", 60), ("10x", 600)):
        notes, names, sdir = build(tmp_path, monkeypatch, n, tag=tag)
        rig = Rig(monkeypatch, notes, names)
        flag(monkeypatch, False)
        base = [ask_it(q, sdir, capsys)[1] for q, *_ in PLANTED]
        sync(sdir)
        flag(monkeypatch, True)
        worst, got = 0, []
        for q, fname, _ in PLANTED:
            rig.opened.clear(); rig.panel_calls = 0
            _rc, out = ask_it(q, sdir, capsys)
            worst = max(worst, len(rig.opened))
            got.append(out)
            assert rig.panel_calls == 0, "the pointer list comes from the index, not the engine panel"
            assert fname in out
        hashed[tag], tops[tag] = worst, [top5(o) for o in got]
        assert [top5(o) for o in base] == tops[tag]  # same top 5, flag on vs off, every question
    assert hashed["1x"] <= 20 and hashed["10x"] <= 20, hashed
    assert (tmp_path / "10x" / "notes").exists()


def test_edited_file_is_detected_at_read(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    flag(monkeypatch, True)
    target = next(notes.rglob("zorblax.md"))
    target.write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    _rc, out = ask_it(PLANTED[0][0], sdir, capsys)
    stage = json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]
    assert str(target) in stage["mismatch"] and stage["verified"] <= 20
    from file_index import FileIndex
    assert FileIndex(PRINCIPAL, sdir / "index.sqlite").is_stale(target.parent.name)  # marked for the updater


def test_missing_index_falls_back_with_reason(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    rig = Rig(monkeypatch, notes, names)
    flag(monkeypatch, False)
    base = ask_it(PLANTED[1][0], sdir, capsys)[1]
    flag(monkeypatch, True)
    rc, out = ask_it(PLANTED[1][0], sdir, capsys)
    assert top5(out) == top5(base) and "plimbus.md" in out
    assert rig.panel_calls >= 1  # today's path ran
    stage = json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]
    assert stage["used"] is False and stage["fallback"] == "index missing"


@pytest.mark.parametrize("damage,reason", [("corrupt", "index corrupt"), ("old", "index stale")])
def test_corrupt_or_stale_index_falls_back(tmp_path, monkeypatch, capsys, damage, reason):
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    Rig(monkeypatch, notes, names)
    sync(sdir)
    if damage == "corrupt":
        (sdir / "index.sqlite").write_bytes(b"not a database" * 50)
    else:
        from file_index import FileIndex
        i = FileIndex(PRINCIPAL, sdir / "index.sqlite")
        i.db.execute("UPDATE meta SET v='1'"); i.db.commit(); i.close()
    flag(monkeypatch, True)
    _rc, out = ask_it(PLANTED[2][0], sdir, capsys)
    stage = json.loads(Path(sdir / "traces.jsonl").read_text().splitlines()[-1])["stages"]["index"]
    assert stage["used"] is False and stage["fallback"].startswith(reason) and "wumpet.md" in out


def test_updater_is_detached_not_inline(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 30)
    Rig(monkeypatch, notes, names)
    spawned = []
    monkeypatch.setattr(ask, "spawn_index_updater", lambda p: spawned.append(p))
    flag(monkeypatch, True)
    ask_it(PLANTED[3][0], sdir, capsys)  # no index yet: fallback, and an updater is started
    assert spawned == [PRINCIPAL] and not (sdir / "index.sqlite").exists()
    ask_it(PLANTED[3][0], sdir, capsys)  # throttled by the stamp: not again at once
    assert spawned == [PRINCIPAL]


def test_edited_file_after_sync_is_still_served_and_few_files_opened(tmp_path, monkeypatch, capsys):
    notes, names, sdir = build(tmp_path, monkeypatch, 40)
    rig = Rig(monkeypatch, notes, names)
    sync(sdir)
    target = next(notes.rglob("zorblax.md"))
    target.write_text("# zorblax\nzorblax quenth shipment arrives on the ninth of June.\n")
    sync(sdir)  # the updater has now seen the edit: the file sits in `seen`, not `files`
    from file_index import FileIndex
    i = FileIndex(PRINCIPAL, sdir / "index.sqlite")
    assert i.count(path=str(target)) == 0
    assert any(p == str(target) for _ptr, p, _e in i.candidates(names))  # still a candidate (reason edited)
    i.close()
    flag(monkeypatch, True)
    rig.opened.clear()
    _rc, out = ask_it(PLANTED[0][0], sdir, capsys)
    assert "zorblax.md" in out
    assert len(rig.opened) <= 20, len(rig.opened)


def test_saved_answer_check_hashes_nothing_on_a_miss(tmp_path, monkeypatch):
    """Real in-process engine (not stubbed): saved rows exist for other questions, this one misses."""
    import subprocess
    repo = SKILL.parent.parent
    exp = repo / "experiments" / "verified-pointer-memory"
    for name in ("SUPERJEV_REPO", "SUPERJEV_JUDGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    subprocess.run([sys.executable, str(SKILL / "setup.py")], capture_output=True, text=True, cwd=str(tmp_path), timeout=60)
    note = tmp_path / "notes" / "acme.md"
    note.parent.mkdir()
    note.write_text("# Acme refund window\nAcme customers can return an order within 30 days.\n")
    monkeypatch.syspath_prepend(str(exp))
    from cli import load_config
    from path_connect import connect
    import service as svc
    config = load_config(tmp_path / "state" / "_memory" / "config.json")
    draft = connect({"pointer": "acme", "principals": ["ann"], "sources": [{"path": str(note)}]}, config)
    done = connect({"pointer": "acme", "principals": ["ann"], "reviewed": True, "navigationSHA": draft["navigationSHA"],
                    "sources": [{"path": x["path"], "sha256": x["sha256"]} for x in draft["sources"]]}, config)
    assert done.get("status") == "registered", done
    service = svc.Service(config["db"], config["registry"], lambda *_: {"status": "no-match"})
    pointer, err = service.pointer("acme", "ann")
    assert err is None
    with service.connect() as c:  # a saved row for ANOTHER question on this pointer
        c.execute("INSERT INTO cache(k,pointer,generation,fingerprint,body) VALUES(?,?,?,?,?)",
                  (service.key(pointer, "some other question", "ann", "", svc.normalize_freshness(None)),
                   "acme", pointer["generation"], pointer["fingerprint"], "{}"))
    hashed = []
    real = svc.sha
    monkeypatch.setattr(svc, "sha", lambda p: (hashed.append(str(p)), real(p))[1])
    assert service.cached("ann", "what is the acme refund window")["status"] == "cache-miss"
    assert hashed == []  # a miss hashes nothing
    service.cached("ann", "some other question")
    assert hashed  # a row for this question: only then is the pointer snapshotted


def measure(tmp, sizes=(300, 3000), reps=3):
    """Local ask time (warm index, stub judge, no network): prints one row per corpus size."""
    class MP:  # tiny monkeypatch stand-in for the script run
        def __init__(self): self.undo = []
        def setenv(self, k, v): import os; os.environ[k] = v
        def setattr(self, obj, name, val): self.undo.append((obj, name, getattr(obj, name))); setattr(obj, name, val)
    import io, contextlib
    rows = []
    for n in sizes:
        mp = MP()
        notes, names, sdir = build(Path(tmp), mp, n, tag=f"m{n}")
        Rig(mp, notes, names)
        ask.index_sync(PRINCIPAL, sdir)
        for on in (False, True):
            mp.setenv("SUPERJEV_INDEX", "1" if on else "0")
            ts = []
            for _ in range(reps):
                for q, *_ in PLANTED:
                    t = time.time()
                    with contextlib.redirect_stdout(io.StringIO()):
                        ask.lookup(q, PRINCIPAL, sdir)
                    ts.append(time.time() - t)
            rows.append((n + len(PLANTED), "on" if on else "off", sorted(ts)[len(ts) // 2]))
        for obj, name, val in reversed(mp.undo):
            setattr(obj, name, val)
    for files, mode, secs in rows:
        print(f"files={files:6d} flag={mode:3s} median ask {secs:.3f}s")


if __name__ == "__main__":
    import tempfile
    measure(tempfile.mkdtemp())
