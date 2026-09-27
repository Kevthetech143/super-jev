"""Principal-scoped connection status is read-only and never performs retrieval."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask


def test_status_only_displays_principals_pointers(tmp_path, monkeypatch, capsys):
    requests = []
    def memory(req):
        requests.append(req)
        return {"status": "ok", "pointers": [
            {"pointer": "reader-notes", "status": "available", "snapshotStatus": "available"},
            {"pointer": "reader-old", "status": "preparation-required"}],
            "datasets": [{"name": "another-agents-private-set"}]}
    monkeypatch.setattr(ask, "memory", memory)
    monkeypatch.setattr(ask, "refresh_hint", lambda *a: "; refresh this pointer")
    monkeypatch.setattr(ask, "state_dir", lambda p: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "reader", "--status"])
    assert ask._main() == 0
    assert requests == [{"action": "panel", "principal": "reader"}]
    out = capsys.readouterr().out
    assert "reader-notes: ready" in out and "reader-old: stale" in out
    assert "refresh this pointer" in out and "Next:" in out
    assert "another-agents" not in out


@pytest.mark.parametrize("panel, expected, rc", [
    ({"pointers": []}, "Nothing connected", 0),
    ({"status": "error", "reason": "not-set-up"}, "setup.py", 1),
    ({"status": "error", "reason": "provider unavailable"}, "unavailable", 1),
    ({"status": "error", "pointers": []}, "unavailable", 1),
    ({"pointers": ["legacy-notes"]}, "unknown", 0),
])
def test_status_handles_empty_failed_and_legacy_panels(panel, expected, rc, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: panel)
    assert ask.connection_status("reader") == rc
    assert expected in capsys.readouterr().out


def test_status_does_not_turn_extra_arguments_into_a_paid_lookup(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "state_dir", lambda p: tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "reader", "--status", "extra"])
    monkeypatch.setattr(ask, "lookup", lambda *a: pytest.fail("must not search"))
    assert ask._main() == 2
