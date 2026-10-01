#!/usr/bin/env python3
"""share_pointers.py against the real memory runtime (cli.run over a tmp db/registry): sharing an
already-connected pointer adds principals with no provider call, keeps its existing ones, is
idempotent, and a later heal or audit keeps the shared scope.

    python3 -m pytest skills/super-jev/tests/test_share_pointers.py -q
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
EXP = SKILL.parent.parent / "experiments" / "verified-pointer-memory"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(EXP))
import cli  # noqa: E402
import share_pointers as sp  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runtime(tmp_path):
    """A memory() over the real runtime; any provider (retrieve/navigate) run leaves a marker."""
    marker = tmp_path / "provider-called"
    probe = ["python3", "-c", f"open({str(marker)!r}, 'a').write('x')"]
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"db": "answers.sqlite", "registry": "registry.json",
                               "retrievalCommand": probe, "navigationCommand": probe}))
    config = cli.load_config(cfg)
    calls = []

    def memory(req):
        calls.append(req["action"])
        try:
            return cli.run(req, config)
        except ValueError as e:
            return {"status": "error", "reason": str(e)}

    return memory, marker, calls


def _connect(memory, tmp_path, pointer, principals, folder="global/knowledge", shareable=True):
    (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    src = tmp_path / folder / f"{pointer}.md"
    src.write_text(f"# {pointer}\nshared fleet note\n")
    req = {"action": "connect", "pointer": pointer, "principals": principals,
           "sources": [{"path": str(src), "description": "fleet note"}]}
    if shareable is not None:
        req["shareable"] = shareable
    preview = memory(req)
    assert memory({**req, "reviewed": True, "sources": preview["sources"]})["status"] == "registered"
    return src


def _visible(memory, principal):
    return {p["pointer"]: p["status"] for p in memory({"action": "panel", "principal": principal})["pointers"]}


def test_share_adds_principals_with_no_provider_call_and_is_idempotent(tmp_path):
    memory, marker, calls = _runtime(tmp_path)
    _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    _connect(memory, tmp_path, "main-skills-catalog", ["main"])
    _connect(memory, tmp_path, "main-skills-catalog-2", ["main"])
    assert "fleet-knowledge" not in _visible(memory, "otherbot")

    calls.clear()
    out = sp.share(["fleet-knowledge", "main-skills-catalog", "main-skills-catalog-2"], ["otherbot"], memory=memory)
    assert out == {"fleet-knowledge": "shared", "main-skills-catalog": "shared",
                   "main-skills-catalog-2": "shared"}
    assert calls == ["panel", "register", "register", "register"]  # no connect, no search, no navigate
    assert not marker.exists()
    seen = _visible(memory, "otherbot")
    assert all(seen[p] == "available" for p in out)
    assert "fleet-knowledge" in _visible(memory, "owner")  # the owner keeps it
    assert sp.share(["fleet-knowledge"], ["otherbot"], memory=memory) == {"fleet-knowledge": "already"}
    assert sp.share(["main-skills-catalog*"], ["otherbot"], memory=memory) == {"main-skills-catalog*": "unknown-pointer"}  # no globs
    assert sp.share(["fleet-knowledge"], ["x"], dry_run=True, memory=memory) == {"fleet-knowledge": "would-share"}
    assert "fleet-knowledge" not in _visible(memory, "x")


def test_a_stale_pointer_is_not_shared(tmp_path):
    memory, _, _ = _runtime(tmp_path)
    src = _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    src.write_text("# changed\n")
    assert sp.share(["fleet-knowledge"], ["otherbot"], memory=memory)["fleet-knowledge"].startswith("error")
    assert "fleet-knowledge" not in _visible(memory, "otherbot")


def test_share_defaults_reads_the_shared_list(tmp_path, monkeypatch):
    memory, _, _ = _runtime(tmp_path)
    _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    listing = tmp_path / "shared-pointers.json"
    monkeypatch.setenv("SUPERJEV_SHARED_POINTERS", str(listing))
    assert sp.share_defaults(["newbot"], memory=memory) == {}  # no list, no share
    listing.write_text(json.dumps({"pointers": ["fleet-knowledge"]}))
    assert sp.share_defaults(["newbot"], memory=memory) == {"fleet-knowledge": "shared"}
    assert "fleet-knowledge" in _visible(memory, "newbot")


def test_a_shared_pointer_still_heals_and_keeps_its_shared_scope(tmp_path, monkeypatch):
    ah = _load("auto_heal_share_t", SKILL / "auto_heal.py")
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    memory, _, _ = _runtime(tmp_path)
    monkeypatch.setattr(ah, "_memory", memory)
    src = _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    sp.share(["fleet-knowledge"], ["otherbot"], memory=memory)
    src.write_text("# fleet-knowledge\nupdated fleet note\n")
    assert _visible(memory, "otherbot")["fleet-knowledge"] == "preparation-required"
    assert ah.reconnect_recipe("fleet-knowledge", "otherbot") == "reconnected"
    assert _visible(memory, "otherbot")["fleet-knowledge"] == "available"
    assert _visible(memory, "owner")["fleet-knowledge"] == "available"


def test_audit_does_not_flag_a_pointer_on_the_shared_list(tmp_path):
    av = _load("audit_visibility_share_t", SKILL / "audit_visibility.py")
    memory, _, _ = _runtime(tmp_path)
    _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    sp.share(["fleet-knowledge"], ["otherbot"], memory=memory)
    reg, db = tmp_path / "registry.json", tmp_path / "answers.sqlite"
    flags = av.audit(reg, db)["principals"]["otherbot"]["flags"]
    assert [f["type"] for f in flags] == ["visible-but-not-connected"]
    assert av.audit(reg, db, ["fleet-knowledge"])["principals"]["otherbot"]["flags"] == []


def test_a_pointer_with_a_brain_documents_or_profile_source_is_refused(tmp_path):
    memory, _, calls = _runtime(tmp_path)
    _connect(memory, tmp_path, "bot-brain-notes", ["bot"], folder="agents/bot-brain/notes")
    _connect(memory, tmp_path, "bot-docs", ["bot"], folder="home/Documents")
    _connect(memory, tmp_path, "bot-profile", ["bot"], folder="agents/global/profile")
    calls.clear()
    out = sp.share(["bot-brain-notes", "bot-docs", "bot-profile"], ["otherbot"], memory=memory)
    assert out["bot-brain-notes"].startswith("refused:") and "bot's brain" in out["bot-brain-notes"]
    assert out["bot-docs"].startswith("refused:") and "documents/" in out["bot-docs"]
    assert out["bot-profile"].startswith("refused:") and "profile/" in out["bot-profile"]
    assert "register" not in calls
    assert _visible(memory, "otherbot") == {}


def _shareable(tmp_path, pointer):
    return json.loads((tmp_path / "registry.json").read_text())["datasets"][pointer].get("shareable")


def test_a_connection_is_private_until_a_person_marks_it(tmp_path, monkeypatch):
    memory, marker, calls = _runtime(tmp_path)
    monkeypatch.setattr(sp, "memory", memory)
    _connect(memory, tmp_path, "team-notes", ["owner"], folder="shared/team/notes", shareable=None)
    assert _shareable(tmp_path, "team-notes") is False  # recorded private at connect
    calls.clear()
    out = sp.share(["team-notes"], ["otherbot"], memory=memory)
    assert out["team-notes"].startswith("refused: private")
    assert "register" not in calls and _visible(memory, "otherbot") == {}
    assert sp.mark(["team-notes"], memory=memory, principal="owner") == {"team-notes": "marked"}
    assert _shareable(tmp_path, "team-notes") is True
    assert _visible(memory, "owner")["team-notes"] == "available"  # a mark never stales the pointer
    assert sp.share(["team-notes"], ["otherbot"], memory=memory) == {"team-notes": "shared"}
    assert sp.mark(["team-notes"], value=False, memory=memory, principal="owner") == {"team-notes": "unmarked"}
    assert _visible(memory, "owner")["team-notes"] == "available"
    assert sp.share(["team-notes"], ["third"], memory=memory)["team-notes"].startswith("refused: private")
    assert sp.mark(["nope"], memory=memory, principal="owner") == {"nope": "unknown-pointer"}
    assert not marker.exists()


def test_marking_needs_a_named_caller_and_never_assumes_one(tmp_path, monkeypatch, capsys):
    memory, _, calls = _runtime(tmp_path)
    monkeypatch.setattr(sp, "memory", memory)
    monkeypatch.delenv("SUPERJEV_PRINCIPAL", raising=False)
    _connect(memory, tmp_path, "team-notes", ["owner"], folder="shared/team/notes", shareable=None)
    calls.clear()
    out = sp.mark(["team-notes"], memory=memory)
    assert out["team-notes"].startswith("error: no principal") and _shareable(tmp_path, "team-notes") is False
    assert calls == []  # nothing was asked of the runtime under an invented name
    asked = []
    monkeypatch.setattr(sp, "mark", lambda names, value=True, principal=None: asked.append((names, principal)) or {n: "marked" for n in names})
    monkeypatch.setattr(sys, "argv", ["share_pointers.py", "--mark", "team-notes"])
    with pytest.raises(SystemExit) as stop:
        sp.main()
    assert stop.value.code == 2 and "--principal" in capsys.readouterr().err and asked == []
    monkeypatch.setenv("SUPERJEV_PRINCIPAL", "owner")  # the same fallback ask.py uses
    assert sp.main() == 0 and asked == [(["team-notes"], "owner")]
    monkeypatch.setattr(sys, "argv", ["share_pointers.py", "--principal", "other", "--mark", "team-notes"])
    assert sp.main() == 0 and asked[-1] == (["team-notes"], "other")


def test_a_private_source_cannot_be_marked_shareable(tmp_path):
    memory, _, _ = _runtime(tmp_path)
    _connect(memory, tmp_path, "bot-brain-notes", ["bot"], folder="agents/bot-brain/notes", shareable=None)
    out = sp.mark(["bot-brain-notes"], memory=memory, principal="bot")
    assert out["bot-brain-notes"].startswith("refused:") and _shareable(tmp_path, "bot-brain-notes") is False


def test_a_refresh_keeps_the_mark_and_a_heal_keeps_it_too(tmp_path, monkeypatch):
    ah = _load("auto_heal_mark_t", SKILL / "auto_heal.py")
    monkeypatch.setattr(ah, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(ah, "LOG_PATH", tmp_path / "state" / "autoheal.log")
    memory, _, _ = _runtime(tmp_path)
    monkeypatch.setattr(ah, "_memory", memory)
    src = _connect(memory, tmp_path, "fleet-knowledge", ["owner"])
    src.write_text("# fleet-knowledge\nnew bytes\n")
    assert ah.reconnect_recipe("fleet-knowledge", "owner") == "reconnected"  # recipe carries no mark
    assert _shareable(tmp_path, "fleet-knowledge") is True
    priv = _connect(memory, tmp_path, "own-notes", ["owner"], shareable=None)
    priv.write_text("# own\nchanged\n")
    assert ah.reconnect_recipe("own-notes", "owner") == "reconnected"
    assert _shareable(tmp_path, "own-notes") is False
