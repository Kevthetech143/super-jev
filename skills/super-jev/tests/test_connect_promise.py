"""The connect promise, offline: Markdown by default (code files only with --ext), notes up to 250,000 bytes connect whole, bigger ones are held
"too big, split it", and every run ends with CONNECTED/HELD/FAILED counts and an exit code that matches
(0 all connected, 3 something held, 1 something failed)."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pb_promise", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

OK = {"state": "SUPPORTED", "confidence": 0.95, "secs": 0}
COUNTS = re.compile(r"^CONNECTED (\d+), HELD (\d+), FAILED (\d+)\b", re.M)


def _folder(tmp_path, **files):
    root = tmp_path / "notes"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    return root


@pytest.fixture
def go(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("SUPERJEV_BATCH_JEV", "0")
    monkeypatch.setattr(pb, "writer", lambda items, *a, **k: {i["path"]: {"description": "A note.", "question": "q?"} for i in items})
    monkeypatch.setattr(pb, "gate", lambda *a, **k: OK)
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [OK for _ in claims], raising=False)
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": True})

    def run(root, *extra):
        monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "p", "--principal", "alice",
                                          "--no-findability", "--no-shared", "--writer", "claude", *extra])
        code = pb.main()
        return code, capsys.readouterr().out
    return run


def test_note_of_exactly_250000_bytes_connects_whole_and_one_byte_more_is_held(tmp_path):
    root = _folder(tmp_path)
    (root / "exact.md").write_text("a" * 250_000)
    (root / "over.md").write_text("a" * 250_001)
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["exact.md"]
    assert len(held) == 1 and held[0][0].endswith("over.md")
    assert "too big, split it" in held[0][1] and "250,001 bytes" in held[0][1]


def test_size_rule_has_one_number_no_sections_no_size_approval():
    for gone in ("SECTION_MAX_BYTES", "split_sections", "section_text", "APPROVE_MAX_BYTES",
                 "extension_list", "CODE_EXTENSIONS", "code_heading"):
        assert not hasattr(pb, gone), gone
    assert pb.CEILING_BYTES == 250_000 and pb.CONNECTABLE_EXTENSIONS == (".md",)


@pytest.mark.parametrize("flag", [["--approve-held", "x.md"]])
def test_removed_flags_are_refused(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(tmp_path), "--pointer", "p", "--principal", "a", *flag])
    with pytest.raises(SystemExit) as exc:
        pb.main()
    assert exc.value.code == 2


def test_only_markdown_is_inventoried(tmp_path):
    root = _folder(tmp_path, **{"a.md": "note\n", "b.py": "x = 1\n", "c.txt": "text\n"})
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["a.md"] and not held


def test_all_connected_ends_with_counts_and_exits_0(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "b.md": "beta\n"})
    code, out = go(root)
    assert code == 0
    assert out.strip().splitlines()[-1].startswith("CONNECTED 2, HELD 0, FAILED 0")


def test_a_held_file_exits_3_and_the_counts_line_is_last(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    (root / "big.md").write_text("a" * 250_001)
    code, out = go(root)
    assert code == 3
    last = out.strip().splitlines()[-1]
    assert COUNTS.match(last) and last.startswith("CONNECTED 1, HELD 1, FAILED 0")
    assert "too big, split it" in out


def test_a_secret_held_file_exits_3(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "s.md": "pass" + "word: hunter2xyz\n"})
    code, out = go(root)
    assert code == 3 and out.strip().splitlines()[-1].startswith("CONNECTED 1, HELD 1, FAILED 0")


def test_a_file_the_gate_refuses_is_failed_and_exits_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    monkeypatch.setattr(pb, "gate", lambda *a, **k: {"state": "CONTRADICTED", "confidence": 0.9, "secs": 0})
    monkeypatch.setattr(pb, "gate_many", lambda p, claims, *a, **k: [{"state": "CONTRADICTED", "confidence": 0.9, "secs": 0} for _ in claims], raising=False)
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 1")


def test_a_part_that_does_not_connect_is_failed_and_exits_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "b.md": "beta\n"})
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 2")


def test_failed_and_held_together_exit_1(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    (root / "big.md").write_text("a" * 250_001)
    monkeypatch.setattr(pb, "connect_part", lambda *a, **k: {"connected": False})
    code, out = go(root)
    assert code == 1 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 1, FAILED 1")


def test_no_connect_preview_still_ends_with_counts(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    code, out = go(root, "--no-connect")
    assert code == 0 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 0, FAILED 0")
    assert "--no-connect" in out.strip().splitlines()[-1]


def test_every_file_held_still_ends_with_counts_and_is_nonzero(tmp_path, go):
    root = _folder(tmp_path)
    (root / "big.md").write_text("a" * 250_001)
    code, out = go(root)
    assert code != 0 and out.strip().splitlines()[-1].startswith("CONNECTED 0, HELD 1, FAILED 0")


def test_a_recipe_with_py_refreshes_and_replays_its_extensions(tmp_path, go, monkeypatch):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "parse_totals.py": "def parse_totals():\n    return 1\n"})
    code, text = go(root, "--ext", "py")
    assert code == 0 and "CONNECTED 2" in text
    rp = tmp_path / "cache" / "p-report.json"
    assert json.loads(rp.read_text())["extensions"] == [".md", ".py"]
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "p", "--principal", "alice", "--refresh",
                                      "--no-findability", "--no-shared", "--writer", "claude"])
    assert pb.main() == 0
    assert json.loads(rp.read_text())["extensions"] == [".md", ".py"]


def test_without_ext_code_files_stay_out(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "parse_totals.py": "x = 1\n"})
    code, text = go(root)
    assert code == 0 and "CONNECTED 1" in text


def test_ext_naming_a_credential_suffix_is_refused_by_name(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n"})
    code, text = go(root, "--ext", "py,PEM")
    assert code == 2 and "REFUSED" in text and ".pem" in text


def test_a_secret_named_code_file_is_held_by_name(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "deploy-secret.py": "x = 1\n"})
    code, text = go(root, "--ext", "py")
    assert code == 3 and "HELD" in text and "deploy-secret.py" in text


@pytest.mark.parametrize("flag", ["", ",", "py,,ts"])
def test_an_empty_ext_suffix_is_refused_by_name(tmp_path, go, flag):
    code, text = go(_folder(tmp_path, **{"a.md": "alpha\n"}), "--ext", flag)
    assert code == 2 and "REFUSED" in text and "--ext" in text


def test_ext_pem_with_json_prints_a_usage_refusal_object(tmp_path, go):
    code, text = go(_folder(tmp_path, **{"a.md": "alpha\n"}), "--ext", "pem", "--json")
    obj = json.loads(text.strip().splitlines()[-1])
    assert code == 2 and obj["refused"]["kind"] == "usage" and ".pem" in obj["refused"]["why"]


@pytest.mark.parametrize("recorded", [[".md", ""], ".py", [".md", ".pem"], [".py", 3]])
def test_a_recorded_bad_extension_list_is_refused_not_widened(tmp_path, go, monkeypatch, recorded):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "tool_one.py": "x = 1\n"})
    assert go(root)[0] == 0
    rp = tmp_path / "cache" / "p-report.json"
    rep = json.loads(rp.read_text()); rep["extensions"] = recorded; rp.write_text(json.dumps(rep))
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--pointer", "p", "--principal", "alice", "--refresh"])
    assert pb.main() == 2
    assert json.loads(rp.read_text())["extensions"] == recorded


def test_the_refresh_line_and_skip_wording_show_the_suffixes(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "b.py": "x = 1\n", "c.txt": "t\n"})
    code, text = go(root, "--ext", "py")
    assert "only .md, .py files connect" in text and "only .md files connect" not in text


@pytest.mark.parametrize("line", ["DB = 'postgres://appuser:hunter22x@db.example.test/main'",
                                  "url: redis://:s3cretpw9@cache.example.test:6379",
                                  "WEBHOOK = 'https://hooks.slack.com/services/T0AAAAAAA/B0BBBBBBB/abcDEF123456'"])
@pytest.mark.parametrize("name", ["conf.py", "conf.md"])
def test_connection_string_and_webhook_secrets_are_held(tmp_path, go, name, line):
    root = _folder(tmp_path, **{"a.md": "alpha\n", name: line + "\n"})
    code, text = go(root, "--ext", "py")
    assert code == 3 and re.search(rf"HELD\s+{re.escape(name)}\s", text) and len(set(re.findall(r"^  HELD\s+(\S+)", text, re.M))) == 1
    assert "CONNECTED 1, HELD 1, FAILED 0" in text
    assert "hunter22x" not in text and "s3cretpw9" not in text


@pytest.mark.parametrize("line", ["postgres://user:password@localhost:5432/app", "postgresql://USER:PASS@HOST:5432/DB",
                                  "amqp://guest:guest@localhost:5672", "http://localhost:3000?email=foo@bar.com",
                                  "https://example.com:8443?to=a@b.com"])
def test_placeholder_connection_strings_are_not_held(tmp_path, go, line):
    code, text = go(_folder(tmp_path, **{"a.md": "alpha\n", "n.md": line + "\n"}))
    assert code == 0 and "CONNECTED 2, HELD 0" in text


def test_a_backup_named_code_file_has_no_rerun_line_and_a_big_one_no_section_hint(tmp_path, go):
    root = _folder(tmp_path, **{"a.md": "alpha\n", "x.bak.py": "x = 1\n", "big_tool.py": "x = 1\n" * 60000})
    code, text = go(root, "--ext", "py")
    assert code == 3 and "x.bak.py" in text and "big_tool.py" in text
    assert "then run" not in text.split("x.bak.py")[1].split("\n")[1] and "## section" not in text
