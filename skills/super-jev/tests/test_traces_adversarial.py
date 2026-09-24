"""Adversarial holes in traces/voice (reviewer). Expected to FAIL until fixed."""
import importlib.util, json
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ask_adv", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def test_secret_in_dict_key_path_is_redacted(tmp_path):
    # content_check/routing are keyed by path; _redact only scans values.
    secret_path = "/notes/password=hunter2hunter2.txt"
    assert ask.has_secret(secret_path)
    ask.write_trace(tmp_path, kind="trace", lookup_id="x", question="q",
                    content_check={secret_path: {"score": 1.0, "label": "confirmed"}})
    assert "hunter2" not in (tmp_path / "traces.jsonl").read_text()


def test_stale_cache_hit_withholds_and_falls_through_to_live_search(tmp_path, monkeypatch, capsys):
    # Cache hit whose source changed -> stale answer withheld, then a fresh live search.
    src = tmp_path / "src.txt"; src.write_text("new")
    rec = ask.manual_record_path(tmp_path, "alice", "q")
    rec.parent.mkdir(parents=True)
    rec.write_text(f"source_path: {src}\nsource_sha256: {'0'*64}\n")
    calls = []
    def fake(r):
        calls.append(r["action"])
        if r["action"] == "cached":
            return {"status": "verified-cache-hit", "answer": "OLD-ANSWER"}
        return {"status": "ok", "pointers": []}
    monkeypatch.setattr(ask, "memory", fake)
    rc = ask.lookup("q", "alice", tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "panel" in calls  # live search ran
    assert "OLD-ANSWER" not in out
    assert out.rstrip("\n").splitlines()[-1] == ask.VOICE_LINE


def test_miss_after_cache_hit_does_not_relabel_an_older_live_lookup(tmp_path, monkeypatch):
    # Old live lookup was approved "right". Later the cached answer is served
    # (no trace) and the user --miss's it: the outcome lands on the OLD live
    # trace, flipping a correct ranking to "wrong" in the report.
    ask.write_trace(tmp_path, kind="trace", lookup_id="old", question="q", final_ranked=[])
    ask.write_outcome(tmp_path, "old", "q", "right", file="/a")
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "verified-cache-hit", "answer": "a"})
    ask.lookup("q", "alice", tmp_path)
    monkeypatch.setattr(ask, "memory", lambda r: {"status": "not-found"})
    ask.miss("alice", "q", "/real", tmp_path)
    oc = ask.last_outcome(tmp_path, "old")
    assert oc["result"] == "right"


def test_last_lookup_id_reads_rotated_file(tmp_path):
    (tmp_path / "traces.jsonl.1").write_text(json.dumps({"kind": "trace", "lookup_id": "rot", "question": "q"}) + "\n")
    (tmp_path / "traces.jsonl").write_text("")
    assert ask.last_lookup_id(tmp_path, "q") == "rot"


def test_trace_write_failure_does_not_crash(tmp_path):
    (tmp_path / "traces.jsonl").mkdir()  # makes open() fail with OSError
    ask.write_trace(tmp_path, kind="trace", lookup_id="x", question="q")


def test_trace_report_rejects_nonpositive_days(tmp_path, capsys):
    assert ask.trace_report(tmp_path, 0) == 2
    assert ask.trace_report(tmp_path, -3) == 2
