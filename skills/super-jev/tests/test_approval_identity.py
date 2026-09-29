"""Explicit approvals record the caller principal without inventing a human."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ask


def test_explicit_approval_records_caller_and_replays_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ask, "memory", lambda req: {"status": "saved"})
    ticket = {"approvalTicket": "test-ticket", "passages": []}
    assert ask.send_approval("reader-agent", "question", "answer", "notes", ticket, tmp_path) == 0
    assert ask.approver(tmp_path, "question")["approved_by"] == "principal:reader-agent"
    ask.print_hit({"answer": "answer"}, tmp_path, "reader-agent", "question")
    assert "approved_by: principal:reader-agent" in capsys.readouterr().out


def test_existing_labels_are_preserved_and_missing_identity_is_unknown(tmp_path):
    for who in ("human", "auto-check", "auto-save"):
        ask.record_approver(tmp_path, who, who)
        assert ask.approver(tmp_path, who)["approved_by"] == who
    assert ask.approver(tmp_path, "no recorded identity")["approved_by"] == "unknown (legacy)"
