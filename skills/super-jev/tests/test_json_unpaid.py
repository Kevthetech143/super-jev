#!/usr/bin/env python3
"""A refused-for-payment refresh under --json prints the clean payment message, not an internal error.

    python3 -m pytest skills/super-jev/tests/test_json_unpaid.py -q
"""
import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("prepare_bulk_unpaid", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def test_json_run_payment_required_is_a_clean_failure(tmp_path, monkeypatch, capsys):
    def unpaid(a):
        raise pb.PaymentRequired("the judge refused the call for payment (HTTP 402); top up, then refresh again")

    monkeypatch.setattr(pb, "run", unpaid)
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pb, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(tmp_path), "--pointer", "notes",
                                      "--principal", "me", "--json"])
    assert pb.main() == 1
    cap = capsys.readouterr()
    assert "Traceback" not in cap.err
    assert "internal error" not in cap.out
    assert "HTTP 402" in cap.out
    json.loads(cap.out.strip().splitlines()[-1])
