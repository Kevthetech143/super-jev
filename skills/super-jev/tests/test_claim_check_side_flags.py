"""An explicit --claim check (check, ask --answer) is decided by its claim rows.

Fleet hand tests 2026-09-24: a SUPPORTED 1.00 claim against a plain internal
note exited 3 (READ) because the draft-level rows (HAS_LEAKS,
SELF_CONTRADICTORY) misfired on the note, so --answer never saved. A wrong
claim must still block."""
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jc = load("jev_client_side", SKILL / "lib" / "jev_client.py")
sj = load("superjev_side", SKILL / "superjev.py")


def run_main(monkeypatch, tmp_path, claim_verdict, side, argv_extra):
    rows = [{"key": "c1", "flag": claim_verdict != "SUPPORTED"},
            {"key": "leaked_internal", "flag": True},
            {"key": "self_contradictory", "flag": True},
            {"key": "overclaim", "flag": side == "overclaim"}]
    monkeypatch.setattr(jc, "check", lambda e, c, d: (rows, {}, 3))
    monkeypatch.setattr(jc, "print_table", lambda *a: None)
    ev = tmp_path / "note.md"
    ev.write_text("breakeven is 3.94 per share\n")
    return jc.main([str(ev), *argv_extra])


def test_client_claim_check_ignores_note_side_flags(monkeypatch, tmp_path):
    assert run_main(monkeypatch, tmp_path, "SUPPORTED", None,
                    ["--claim", "breakeven is 3.94 per share"]) == 0


def test_client_wrong_claim_still_blocks(monkeypatch, tmp_path):
    assert run_main(monkeypatch, tmp_path, "CONTRADICTED", None,
                    ["--claim", "breakeven is 9.99 per share"]) == 3


def test_client_overclaim_still_blocks_a_claim(monkeypatch, tmp_path):
    assert run_main(monkeypatch, tmp_path, "SUPPORTED", "overclaim",
                    ["--claim", "breakeven is 3.94 per share"]) == 3


def test_client_draft_check_still_blocks_on_side_flags(monkeypatch, tmp_path):
    d = tmp_path / "d.md"
    d.write_text("breakeven is 3.94 per share\n")
    assert run_main(monkeypatch, tmp_path, "SUPPORTED", None, ["--draft", str(d)]) == 3


OUT = ("  c1   {v:14s} 1.00  x\n\n"
       "  leaked_internal    HAS_LEAKS            0.73\n"
       "  self_contradictory SELF_CONTRADICTORY   0.88\n")


def test_gate_claim_rows_decide_for_explicit_claims():
    assert sj.gate_fail_closed(0, OUT.format(v="SUPPORTED"), 1) == 0
    assert sj.gate_fail_closed(0, OUT.format(v="CONTRADICTED"), 1) == 3
    # a draft run (no explicit claims) still blocks on side flags
    assert sj.gate_fail_closed(0, OUT.format(v="SUPPORTED"), 0) == 3
    # overclaim still blocks an explicit claim
    assert sj.gate_fail_closed(0, OUT.format(v="SUPPORTED") + "  overclaim  OVERCLAIMS  0.97\n", 1) == 3
