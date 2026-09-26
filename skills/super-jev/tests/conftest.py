import pytest


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
def _no_none_choice(monkeypatch):
    """Lookups never make the live "none of these" Jev call in tests; test_none_choice.py
    turns it on with a faked judge."""
    monkeypatch.setenv("SUPERJEV_NONE_CHOICE", "0")
