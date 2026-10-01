"""Remember-it works on a fresh install (contract: Repeat questions). Every check starts from the
memory config setup.py itself writes -- never a hand-written one -- so a default promise that only
works with a setting the maintainer's machine happens to have fails here. Made-up user and note;
only fetch is stubbed (no key, no network), as in test_ask_to_node_end_to_end.py.

    python3 -m pytest skills/super-jev/tests/test_fresh_install_saves.py -q
"""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "super-jev"
EXP = REPO / "experiments" / "verified-pointer-memory"
STUB = """
globalThis.fetch = async (_url, opts) => {
  const payload = JSON.parse(opts.body);
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_0')) || keys[0];
    return [id, {type: 'choice', choice, confidence: 0.95,
      probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 0.95 : 0.05 / (keys.length - 1 || 1)]))}];
  }));
  return new Response(JSON.stringify({model: 'offline', answers}), {status: 200});
};
"""
Q = "What is the Acme refund window?"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A fresh install: setup.py writes the config, one made-up note is connected for principal ann."""
    if subprocess.run(["node", "--version"], capture_output=True).returncode:
        pytest.skip("node is not installed")
    hook = tmp_path / "offline.cjs"
    hook.write_text(STUB)
    for name in ("SUPERJEV_REPO", "SUPERJEV_JUDGE", "SUPERJEV_AUTO_CACHE", "SUPERJEV_SAVE_AFTER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-not-real")
    monkeypatch.setenv("NODE_OPTIONS", f"--require={hook}")
    subprocess.run([sys.executable, str(SKILL / "setup.py")], capture_output=True, text=True,
                   cwd=str(tmp_path), timeout=60)
    config_file = tmp_path / "state" / "_memory" / "config.json"
    assert config_file.is_file()
    note = tmp_path / "notes" / "acme-refunds.md"
    note.parent.mkdir()
    note.write_text("# Acme refund window\nAcme customers can return an order within 30 days of delivery.\n")
    monkeypatch.syspath_prepend(str(EXP))
    from cli import load_config
    from path_connect import connect
    config = load_config(config_file)
    draft = connect({"pointer": "acme", "principals": ["ann"], "sources": [{"path": str(note)}]}, config)
    done = connect({"pointer": "acme", "principals": ["ann"], "reviewed": True,
                    "navigationSHA": draft["navigationSHA"],
                    "sources": [{"path": x["path"], "sha256": x["sha256"]} for x in draft["sources"]]}, config)
    assert done.get("status") == "registered", done
    return tmp_path, note


def ask_cli(tmp_path, *args):
    out = subprocess.run([sys.executable, str(SKILL / "ask.py"), "--principal", "ann", *args],
                         capture_output=True, text=True, cwd=str(tmp_path), timeout=170)
    return out.stdout + out.stderr


def test_a_repeat_question_saves_itself_on_a_fresh_install(fresh):
    tmp_path, note = fresh
    first, second, third = (ask_cli(tmp_path, Q) for _ in range(3))
    assert "OUTCOME: found" in first, first
    assert "not saved" not in first + second + third, second
    assert "Saved for next time" in second, second
    assert "CACHE HIT" in third and "approved_by: auto-save" in third, third
    assert f"saved answer, from {note}" in third, third


def test_approve_saves_at_once_on_a_fresh_install(fresh):
    tmp_path, note = fresh
    spec = importlib.util.spec_from_file_location("ask_fresh_install", SKILL / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    sdir = tmp_path / "state" / "ann"
    ask.log(sdir, "lookup", question=ask.norm_q(Q), top=[{"score": 0.95, "path": str(note), "pointer": "acme"}])
    ask.run_gate = lambda claim, path, passage=None: ("CLEAN", 0.95)  # the claim check's Jev call only
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        rc = ask.approve("ann", Q, "Acme refunds within 30 days of delivery.", sdir)
    assert rc == 0, printed.getvalue()
    assert "approve: saved" in printed.getvalue()
    assert ask.memory({"action": "cached", "principal": "ann", "question": ask.norm_q(Q)})["status"] == "verified-cache-hit"


def test_add_saves_on_a_fresh_install_when_search_misses_its_record(fresh):
    """--add's fallback (cite the record's own reviewed lines) must not need a setting either."""
    tmp_path, _note = fresh
    spec = importlib.util.spec_from_file_location("ask_fresh_add", SKILL / "ask.py")
    ask = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ask)
    real = ask.memory

    def search_misses(req):  # the real store throughout; only the search verdict reads as a miss
        out = real(req)
        if req.get("action") == "search" and out.get("attemptId"):
            out = {**out, "status": "no-match", "passages": []}
        return out

    ask.memory = search_misses
    sdir = tmp_path / "state" / "ann"
    question = "Which day does the Acme office close early?"
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        rc = ask.add_manual("ann", question, "The Acme office closes early on Fridays.", None, sdir)
    assert rc == 0, printed.getvalue()
    assert "approve: saved" in printed.getvalue()
    assert real({"action": "cached", "principal": "ann", "question": ask.norm_q(question)})["status"] == "verified-cache-hit"
