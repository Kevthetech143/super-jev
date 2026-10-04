"""With SUPERJEV_STATE_DIR and HOME in temp dirs, the memory path (memory.sh bypassed) and prepare_bulk write only under them."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
import dispatch  # noqa: E402
import prepare_bulk as pb  # noqa: E402

PINNED = "/Users/admin/super-jev/.local/pointer-memory/config.json"


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _setup(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    (state / "_memory").mkdir(parents=True)
    src = tmp_path / "reviewed.txt"
    src.write_text("The synthetic launch policy is blue.\n")
    rec = {"id": "policy", "description": "Synthetic policy", "path": str(src), "contentSHA": _sha(src)}
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"descriptionsAffirmed": True, "expectedPolicy": "reviewed", "sources": [rec],
        "preparations": [{**rec, "sourceId": "policy", "startLine": 1, "endLine": 1,
                          "reviewedText": src.read_text(), "policy": "reviewed", "status": "reviewed"}]}))
    reg = state / "_memory" / "registry.json"
    reg.write_text(json.dumps({"datasets": {"ds": {"description": "d", "manifestPath": str(man),
        "manifestSHA256": _sha(man), "originals": [{"path": str(src), "sha256": rec["contentSHA"]}]}}}))
    provider = tmp_path / "provider.py"
    provider.write_text("import sys\nsys.stdout.write(%r)\n" % json.dumps({"status": "ready", "passages": [{
        "sourceId": "policy", "path": str(src), "contentSHA": rec["contentSHA"], "startLine": 1, "endLine": 1,
        "reviewedText": src.read_text()}]}))
    (state / "_memory" / "config.json").write_text(json.dumps({
        "db": str(state / "_memory" / "answers.sqlite"), "registry": str(reg),
        "retrievalCommand": [sys.executable, str(provider)]}))
    env = {**os.environ, "HOME": str(home), "SUPERJEV_STATE_DIR": str(state)}
    for k in ("SUPERJEV_REPO", "SUPERJEV_MEMORY_WRAPPER_ACTIVE"):
        env.pop(k, None)
    return home, state, env


def _mem(env, req):
    # The same command ask.py/dispatch build; with SUPERJEV_STATE_DIR set it never goes through an installed memory.sh.
    cmd = dispatch.command(SKILL, "memory", ["--input", "/dev/stdin"])
    r = subprocess.run(cmd, input=json.dumps(req), capture_output=True, text=True, env=env)
    return json.loads(r.stdout)


def test_memory_sh_add_approve_stays_in_temp(tmp_path, monkeypatch):
    home, state, env = _setup(tmp_path)
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    monkeypatch.delenv("SUPERJEV_REPO", raising=False)
    monkeypatch.delenv("SUPERJEV_MEMORY_WRAPPER_ACTIVE", raising=False)
    # Guard before any write: neither resolver may point at the pinned live config.
    assert PINNED not in " ".join(dispatch.command(SKILL, "memory", ["--input", "/dev/stdin"]))
    import ask
    assert ask._engine_target()[1] == state / "_memory" / "config.json"

    assert _mem(env, {"action": "register", "pointer": "p", "dataset": "ds", "principals": ["alice"]})["status"] == "registered"
    ready = _mem(env, {"pointer": "p", "question": "What is the launch policy?", "principal": "alice"})
    assert ready["status"] == "ready"
    saved = _mem(env, {"action": "approve", "ticket": ready["approvalTicket"], "principal": "alice", "approved": True,
                       "answer": "Blue.", "evidence": [{"sourceId": "policy", "quote": "blue"}]})
    assert saved["status"] == "saved"
    assert (state / "_memory" / "answers.sqlite").stat().st_size > 0
    assert not (home / ".local").exists()  # the default path for the fake HOME was never created


def test_prepare_bulk_state_writes_stay_in_temp(tmp_path, monkeypatch):
    home, state, _ = _setup(tmp_path)
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    monkeypatch.setenv("HOME", str(home))
    assert pb._state_dir("alice") == state / "alice"
    assert dispatch.config_path() == state / "_memory" / "config.json"
    assert not (home / ".local").exists()


def test_prepare_bulk_run_leaves_fake_home_alone(tmp_path):
    home, state, env = _setup(tmp_path)
    manual = state / "alice" / "manual"
    manual.mkdir(parents=True)
    (manual / "alice-manual-1.md").write_text("# q\n\nkind: record\nstatus: active\nas_of: 2026-01-01\nsubject: s\n\nbody\n")
    r = subprocess.run([sys.executable, str(SKILL / "prepare_bulk.py"), "--list", "--principal", "alice"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "alice-manual-1" in r.stdout, r.stdout + r.stderr
    assert not (home / ".local").exists()
