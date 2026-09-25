import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_code_ask: uses the real fleet jev lib; skips when the lib file is absent")


@pytest.fixture(autouse=True)
def _one_call_per_pointer(monkeypatch):
    """These suites fake one navigate/confirm call per pointer or file: the path
    SUPERJEV_BATCH_JEV=0 keeps and a failed batch falls back to. The batched path
    has its own tests (test_batch_jev.py), which turn it back on."""
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
