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

def pytest_configure(config):
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
