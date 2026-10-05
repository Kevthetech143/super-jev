import json
import os
from pathlib import Path

import pytest


def _scrub_callers_settings():
    """The suite gives the same result whatever the caller's shell exports. Before any test module
    loads, clear the product's own settings: every SUPERJEV_* except SUPERJEV_TEST_* (developer test
    knobs), every judge profile's key and url variable, and SWEEP_BATCH (the one product setting without
    the prefix). The names come from judge_profiles.json, read directly: importing judge_profile first
    would read SUPERJEV_JUDGE before it is cleared. test/clean-env.ts is the same rule for npm test."""
    profiles = json.loads((Path(__file__).resolve().parent.parent / "judge_profiles.json").read_text())["profiles"]
    names = {"SWEEP_BATCH"}
    for profile in profiles.values():
        names.update(v for v in (profile.get("key_env"), profile.get("api_url_env")) if v)
    names.update(k for k in os.environ if k.startswith("SUPERJEV_") and not k.startswith("SUPERJEV_TEST_"))
    for name in names:
        os.environ.pop(name, None)


_scrub_callers_settings()

# Heal state and prepare locks resolve to <state root> at import, and the scrub above leaves that the
# real ~/.local/state/super-jev. A test must never write there: the fixture below points every loaded
# copy at tmp, and the session-end check fails the run if the real folders appeared anyway.
_REAL_HEAL_DIRS = [Path.home() / ".local/state/super-jev" / n for n in ("locks", "autoheal-state")]
_HEAL_DIRS_BEFORE = [d.exists() for d in _REAL_HEAL_DIRS]
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _heal_state_in_tmp(monkeypatch, tmp_path):
    import gc
    import sys
    import types
    # importlib copies (spec_from_file_location) are not in sys.modules; find them by type, once.
    if "mods" not in _heal_state_in_tmp.__dict__:
        _heal_state_in_tmp.mods = [o for o in gc.get_objects() if isinstance(o, types.ModuleType)]
    for mod in list(sys.modules.values()) + _heal_state_in_tmp.mods:
        f = getattr(mod, "__file__", None)
        if not f or not f.startswith(_SKILL_ROOT) or "/tests/" in f:
            continue
        if hasattr(mod, "LOCK_DIR"):
            monkeypatch.setattr(mod, "LOCK_DIR", tmp_path / "heal-locks")
        if hasattr(mod, "_OLD_STATE_DIR"):
            heal = tmp_path / "heal-state"
            monkeypatch.setattr(mod, "STATE_DIR", heal)
            monkeypatch.setattr(mod, "LOG_PATH", heal / "autoheal.log")


def pytest_sessionfinish(session, exitstatus):
    made = [str(d) for d, was in zip(_REAL_HEAL_DIRS, _HEAL_DIRS_BEFORE) if d.exists() and not was]
    if made:
        print(f"\nTEST ISOLATION BREACH: the run created {made} in the real state folder")
        session.exitstatus = 1

def pytest_configure(config):
    config.addinivalue_line("markers", "real_toc: keeps the real TOC search (no replay of faked navigate candidates)")
    config.addinivalue_line("markers", "real_code_ask: uses the real fleet jev lib; skips when the lib file is absent")


@pytest.fixture(autouse=True)
def _one_call_per_pointer(monkeypatch):
    """These suites fake one navigate/confirm call per pointer or file: the path
    SUPERJEV_BATCH_JEV=0 keeps and a failed batch falls back to. The batched path
    has its own tests (test_batch_jev.py), which turn it back on."""
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")


@pytest.fixture(autouse=True)
def _no_skill_catalog(monkeypatch):
    """Lookups never shell out to the live skills connector in tests; test_stale_quiet_and_skills.py
    turns it on with a faked catalog."""
    monkeypatch.setenv("SUPERJEV_SKILLS", "0")


@pytest.fixture(autouse=True)
def _no_live_shared_list(monkeypatch, tmp_path):
    """prepare_bulk shares the deployment's shared-pointers.json after a connect; tests never read
    the live list. test_share_pointers.py points it at its own file."""
    monkeypatch.setenv("SUPERJEV_SHARED_POINTERS", str(tmp_path / "no-shared-pointers.json"))


@pytest.fixture(autouse=True)
def _no_new_file_scan(monkeypatch):
    """Lookups never start the background new-file scan in tests; test_auto_heal.py calls it directly."""
    monkeypatch.setenv("SUPERJEV_NEW_FILE_SCAN", "0")


@pytest.fixture(autouse=True)
def _no_live_chat_config_or_launcher(monkeypatch, tmp_path):
    """--uninstall also removes the chat CLI's config (it holds an API key) and its launcher.
    No test may reach the real ones: both folders point into tmp unless a test sets its own."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-xdg-config"))
    monkeypatch.setenv("SUPERJEV_BIN_DIR", str(tmp_path / "no-bin"))


@pytest.fixture(autouse=True)
def _toc_read_list_replays_old_routing(request, monkeypatch):
    """The TOC search now picks the read list (routing asks Jev nothing). These suites were written when
    the read list came from each pointer's navigate candidates, which they fake: stand in for the TOC
    search by listing those faked candidates (best first), so what they test (ranking, merging, filters
    and messages after the read list) is unchanged. test_toc_search.py and test_zoom_to_part.py
    exercise the real TOC search and are left alone."""
    if request.module.__name__.split(".")[-1] in ("test_toc_search", "test_zoom_to_part") or request.node.get_closest_marker("real_toc"):
        return
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import toc_search

    def replay(question, corpus, hits, ask_hooks, cache_path=None):
        caller = sys._getframe(1)
        mem, principal = caller.f_globals["memory"], caller.f_locals.get("principal")
        rows = []
        for ptr in {p_ for p_, _e in corpus.values()}:
            try:
                out = mem({"action": "navigate", "pointer": ptr, "principal": principal, "question": question,
                           "lastGood": True})
            except Exception:  # noqa: BLE001 -- a suite that fakes no navigate has no routed candidates
                continue
            if isinstance(out, dict) and out.get("status") == "candidates":
                rows += [(c["score"], c["originalPath"]) for c in out.get("candidates") or []
                         if isinstance(c, dict) and c.get("originalPath") in corpus]
        routed = [p for _s, p in sorted(rows, key=lambda r: -r[0])]
        files = list(dict.fromkeys(routed + [p for _s, p, _ptr in hits if p in corpus]))
        top = [(p, sc) for sc, p in sorted(rows, key=lambda r: -r[0])]
        return files, [], {"pick": {"top": top}}

    monkeypatch.setattr(toc_search, "run", replay)

# Tests that guard routing calls a question no longer makes. Skipped with the reason, never deleted.
_DROPPED = "dropped on purpose: no routing calls on question path"
_OLD_ROUTING = {
    "test_content_cost.py::test_pointer_without_any_question_word_is_not_routed": _DROPPED,
    "test_content_cost.py::test_synonym_keeps_the_pointer": _DROPPED,
}


def pytest_collection_modifyitems(items):
    for item in items:
        reason = _OLD_ROUTING.get(item.nodeid.split("tests/")[-1])
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))
