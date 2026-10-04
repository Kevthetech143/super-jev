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

def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _setup(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir(parents=True)
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


def _wrapper_copy(tmp_path, repo):
    """A copy of dispatch.py next to a planted memory.sh that pins a fake 'live' config (the deployment wrapper)."""
    import importlib.util
    skill = tmp_path / "skills" / "super-jev"
    skill.mkdir(parents=True)
    for name in ("dispatch.py", "ask.py"):
        (skill / name).write_text((SKILL / name).read_text())
    (tmp_path / "experiments").symlink_to(repo / "experiments")
    live = tmp_path / "live"
    live.mkdir()
    (live / "config.json").write_text(json.dumps({"db": str(live / "answers.sqlite"), "registry": str(live / "registry.json"),
                                                  "sentinel": "LIVE"}))
    before = (live / "config.json").read_bytes()
    (skill / "memory.sh").write_text('#!/bin/sh\nexec "%s" "%s/experiments/verified-pointer-memory/cli.py" --config "%s" "$@"\n'
                                     % (sys.executable, repo, live / "config.json"))
    spec = importlib.util.spec_from_file_location("dispatch_copy", skill / "dispatch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return skill, live, before, mod


def _mem(mod, skill, env, req):
    cmd = mod.command(skill, "memory", ["--input", "/dev/stdin"])
    r = subprocess.run(cmd, input=json.dumps(req), capture_output=True, text=True, env=env)
    return json.loads(r.stdout)


def test_memory_sh_add_approve_stays_in_temp(tmp_path, monkeypatch):
    home, state, env = _setup(tmp_path / "t")
    skill, live, before, mod = _wrapper_copy(tmp_path / "w", SKILL.parents[1])
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(state))
    monkeypatch.delenv("SUPERJEV_REPO", raising=False)
    monkeypatch.delenv("SUPERJEV_MEMORY_WRAPPER_ACTIVE", raising=False)
    # The planted wrapper must be bypassed when SUPERJEV_STATE_DIR is set.
    assert str(skill / "memory.sh") not in mod.command(skill, "memory", ["--input", "/dev/stdin"])

    assert _mem(mod, skill, env, {"action": "register", "pointer": "p", "dataset": "ds", "principals": ["alice"]})["status"] == "registered"
    ready = _mem(mod, skill, env, {"pointer": "p", "question": "What is the launch policy?", "principal": "alice"})
    assert ready["status"] == "ready"
    saved = _mem(mod, skill, env, {"action": "approve", "ticket": ready["approvalTicket"], "principal": "alice", "approved": True,
                       "answer": "Blue.", "evidence": [{"sourceId": "policy", "quote": "blue"}]})
    assert saved["status"] == "saved"
    assert (state / "_memory" / "answers.sqlite").stat().st_size > 0
    assert sorted(p.name for p in live.iterdir()) == ["config.json"]  # fake live db/registry never created
    assert (live / "config.json").read_bytes() == before
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


def test_prepare_cache_follows_state_dir(tmp_path):
    """Dry inventory with SUPERJEV_STATE_DIR set writes its cache only under it; default location is unchanged."""
    home, state, folder = tmp_path / "home", tmp_path / "state", tmp_path / "docs"
    home.mkdir(); folder.mkdir()
    (folder / "a.md").write_text("hello\n")
    live = SKILL / "prepare-cache"
    before = sorted(p.name for p in live.glob("*")) if live.is_dir() else None
    env = {**os.environ, "HOME": str(home), "SUPERJEV_STATE_DIR": str(state)}
    r = subprocess.run([sys.executable, str(SKILL / "prepare_bulk.py"), "--root", str(folder), "--pointer", "isoprobe",
                        "--principal", "alice", "--limit", "0", "--no-connect"], env=env, capture_output=True, text=True, timeout=120)
    after = sorted(p.name for p in live.glob("*")) if live.is_dir() else None
    assert before == after, r.stderr
    assert (state / "prepare-cache").is_dir(), r.stdout + r.stderr
    code = "import prepare_bulk as p;print(p.CACHE_DIR)"
    out = subprocess.run([sys.executable, "-c", code], cwd=SKILL, capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if k != "SUPERJEV_STATE_DIR"}).stdout.strip()
    assert out == str(live)
