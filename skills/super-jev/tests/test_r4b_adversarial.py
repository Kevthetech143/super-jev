"""Adversarial holes in recall80-r4b (review). Offline, no key."""
import importlib.util, sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_adv", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec); spec.loader.exec_module(ask)


def _memory(cands):
    def fake(req):
        if req["action"] == "cached":
            return {"status": "miss"}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        return {"status": "candidates", "candidates": cands}
    return fake


def _run(tmp_path, monkeypatch, capsys, q, route, content, name="notes.md"):
    f = tmp_path / name
    f.write_text("stale note")
    monkeypatch.setattr(ask, "word_search", lambda q, p: [])
    monkeypatch.setattr(ask, "memory", _memory([{"score": route, "originalPath": str(f)}]))
    monkeypatch.setattr(ask, "confirm", lambda q, ps: ({str(f): content}, set(), None, {}))
    ask.lookup(q, "me", tmp_path / "s")
    return f, capsys.readouterr().out


# Live-money asks without one of the 10 listed money words lost their live gate
# (main treated every "how much" as live).
def test_how_much_money_in_checking_is_live():
    assert ask.is_live_value_question("how much money do I have in checking")


def test_how_much_cash_is_live():
    assert ask.is_live_value_question("how much cash is in my brokerage account")


# Price-shaped near-miss via "how much did we pay" now gets a possible tier.
def test_how_much_did_we_pay_gets_no_possible_tier(tmp_path, monkeypatch, capsys):
    f, out = _run(tmp_path, monkeypatch, capsys, "how much did we pay for the MacBook", 0.3, 0.7)
    assert str(f) not in out


# Opinion + howcount ask route-keeps a file the content check scored 0.
def test_how_many_should_i_buy_no_route_keep(tmp_path, monkeypatch, capsys):
    f, out = _run(tmp_path, monkeypatch, capsys, "how many shares should I buy", 0.95, 0.0)
    assert str(f) not in out
