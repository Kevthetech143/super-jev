#!/usr/bin/env python3
"""Offline tests for connect_checked.py, the gated wrapper around memory connect.

No network, no live Jev call, no live memory backend: both `gate()` and
`memory()` are monkeypatched at the module level for every test except the
two regex-parser tests, which instead stub `subprocess.run` so the real
`gate()` parsing logic runs against captured dispatch.py output text.

    python3 -m pytest skills/super-jev/tests/test_connect_checked.py -q
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "connect_checked.py"

spec = importlib.util.spec_from_file_location("connect_checked", SCRIPT)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def make_source_file(tmp_path, name, content="hello world\n"):
    p = tmp_path / name
    p.write_text(content)
    return p


def make_request(tmp_path, sources, name="connect.json"):
    req = {"action": "connect", "pointer": "my-records", "principals": ["agent"], "sources": sources}
    req_path = tmp_path / name
    req_path.write_text(json.dumps(req))
    return req_path


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_not_supported_refuses_and_never_calls_memory(tmp_path, monkeypatch, capsys):
    f = make_source_file(tmp_path, "a.md")
    req_path = make_request(tmp_path, [{"path": str(f), "description": "bad description"}])

    memory_calls = []
    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "NOT_SUPPORTED", "confidence": 0.69, "secs": 0.1})
    monkeypatch.setattr(cc, "memory", lambda req: memory_calls.append(req) or {"status": "should-not-be-called"})
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path)])

    rc = cc.main()

    assert rc == 1
    assert memory_calls == []
    out = capsys.readouterr().out
    assert "REFUSED" in out


def test_contradicted_refuses_and_never_calls_memory(tmp_path, monkeypatch, capsys):
    f = make_source_file(tmp_path, "a2.md")
    req_path = make_request(tmp_path, [{"path": str(f), "description": "a description the file disproves"}])

    memory_calls = []
    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "CONTRADICTED", "confidence": 0.82, "secs": 0.1})
    monkeypatch.setattr(cc, "memory", lambda req: memory_calls.append(req) or {"status": "should-not-be-called"})
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path)])

    rc = cc.main()

    assert rc == 1
    assert memory_calls == []
    out = capsys.readouterr().out
    assert "REFUSED" in out


def test_unchecked_over_ceiling_refuses(tmp_path, monkeypatch, capsys):
    f = make_source_file(tmp_path, "big.md")
    req_path = make_request(tmp_path, [{"path": str(f), "description": "a huge file"}])

    memory_calls = []
    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "UNCHECKED", "reason": "over 32k-token ceiling; split the file", "secs": 0.1})
    monkeypatch.setattr(cc, "memory", lambda req: memory_calls.append(req) or {"status": "should-not-be-called"})
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path)])

    rc = cc.main()

    assert rc == 1
    assert memory_calls == []
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "UNCHECKED" in out


def test_supported_at_0_79_refuses_default_line_passes_with_custom_line(tmp_path, monkeypatch, capsys):
    f = make_source_file(tmp_path, "c.md")
    req_path = make_request(tmp_path, [{"path": str(f), "description": "borderline description"}])

    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.79, "secs": 0.1})
    monkeypatch.setattr(cc, "memory", lambda req: (_ for _ in ()).throw(AssertionError("memory should not be called")))

    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path), "--check-only"])
    rc = cc.main()
    assert rc == 1
    assert "REFUSED" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path), "--check-only", "--line", "0.70"])
    rc = cc.main()
    assert rc == 0
    assert "ALL PASS" in capsys.readouterr().out


def test_all_pass_calls_memory_twice_preview_then_confirm(tmp_path, monkeypatch, capsys):
    f1 = make_source_file(tmp_path, "one.md", "content one\n")
    f2 = make_source_file(tmp_path, "two.md", "content two\n")
    req_path = make_request(tmp_path, [
        {"path": str(f1), "description": "describes one"},
        {"path": str(f2), "description": "describes two"},
    ])
    sha1, sha2 = sha256_of(f1), sha256_of(f2)

    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1})

    calls = []

    def fake_memory(req):
        calls.append(json.loads(json.dumps(req)))
        if len(calls) == 1:
            return {"status": "preparation-required", "sources": [
                {"path": str(f1), "sha256": sha1},
                {"path": str(f2), "sha256": sha2},
            ]}
        return {"status": "registered", "pointer": "my-records", "sources": req["sources"]}

    monkeypatch.setattr(cc, "memory", fake_memory)
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path)])

    rc = cc.main()

    assert rc == 0
    assert len(calls) == 2
    preview_req, confirm_req = calls
    assert "reviewed" not in preview_req or preview_req.get("reviewed") is not True
    assert confirm_req["reviewed"] is True
    hashes = {s["path"]: s["sha256"] for s in confirm_req["sources"]}
    assert hashes[str(f1)] == sha1
    assert hashes[str(f2)] == sha2
    out = capsys.readouterr().out
    assert "registered" in out


def test_check_only_on_all_pass_exits_0_without_calling_memory(tmp_path, monkeypatch, capsys):
    f = make_source_file(tmp_path, "d.md")
    req_path = make_request(tmp_path, [{"path": str(f), "description": "good description"}])

    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1})
    monkeypatch.setattr(cc, "memory", lambda req: (_ for _ in ()).throw(AssertionError("memory should not be called")))
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path), "--check-only"])

    rc = cc.main()

    assert rc == 0
    assert "ALL PASS" in capsys.readouterr().out


def test_verdicts_json_written_with_sha256_per_file(tmp_path, monkeypatch):
    f1 = make_source_file(tmp_path, "one.md", "content one\n")
    f2 = make_source_file(tmp_path, "two.md", "content two\n")
    req_path = make_request(tmp_path, [
        {"path": str(f1), "description": "describes one"},
        {"path": str(f2), "description": "describes two"},
    ], name="connect2.json")

    monkeypatch.setattr(cc, "gate", lambda desc, path: {"state": "SUPPORTED", "confidence": 0.95, "secs": 0.1})
    monkeypatch.setattr(sys, "argv", ["connect_checked.py", str(req_path), "--check-only"])

    rc = cc.main()
    assert rc == 0

    verdicts_path = req_path.with_suffix(".verdicts.json")
    assert verdicts_path.is_file()
    body = json.loads(verdicts_path.read_text())
    by_path = {v["path"]: v for v in body["verdicts"]}
    assert by_path[str(f1)]["sha256"] == sha256_of(f1)
    assert by_path[str(f2)]["sha256"] == sha256_of(f2)


def test_gate_parses_not_supported_line_from_dispatch_output(tmp_path, monkeypatch):
    f = make_source_file(tmp_path, "e.md")
    sample = "some preamble\n  c1   NOT_SUPPORTED  0.69  some text\ntrailer\n"

    class FakeResult:
        stdout = sample
        stderr = ""

    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: FakeResult())

    verdict = cc.gate("a description", str(f))

    assert verdict["state"] == "NOT_SUPPORTED"
    assert verdict["confidence"] == 0.69


def test_gate_parses_contradicted_line_from_dispatch_output(tmp_path, monkeypatch):
    f = make_source_file(tmp_path, "g.md")
    sample = "some preamble\n  c1   CONTRADICTED  0.82  some text\ntrailer\n"

    class FakeResult:
        stdout = sample
        stderr = ""

    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: FakeResult())

    verdict = cc.gate("a description", str(f))

    assert verdict["state"] == "CONTRADICTED"
    assert verdict["confidence"] == 0.82


def test_gate_parses_ceiling_message_as_unchecked(tmp_path, monkeypatch):
    f = make_source_file(tmp_path, "f.md")
    sample = "checking...\nfile exceeds the 32,768-token ceiling for this gate\n"

    class FakeResult:
        stdout = sample
        stderr = ""

    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: FakeResult())

    verdict = cc.gate("a description", str(f))

    assert verdict["state"] == "UNCHECKED"
    assert "32k-token ceiling" in verdict["reason"]
