"""connect_github.py with a stubbed gh: export shape, secret drop, refresh window, wiring."""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import connect_github as cg  # noqa: E402

FAKE_KEY = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class StubGh:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        a = " ".join(args)
        if args[:2] == ["pr", "list"]:
            return json.dumps([{"number": 7}])
        if args[:2] == ["pr", "view"]:
            return json.dumps({"number": 7, "title": "Fix symlink crash in --add", "state": "MERGED",
                               "author": {"login": "kev"}, "mergedAt": "2026-09-20T00:00:00Z",
                               "body": f"Resolve symlinks before hashing.\ntoken leak {FAKE_KEY}",
                               "reviews": [{"author": {"login": "rev"}, "state": "APPROVED", "body": "lgtm"}],
                               "comments": [{"author": {"login": "bob"}, "body": "tried before in #3"}]})
        if args[0] == "api" and "/pulls/7/comments" in a:
            return json.dumps([[{"user": {"login": "rev"}, "path": "ask.py", "body": "use realpath"}]])
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 9}])
        if args[:2] == ["issue", "view"]:
            return json.dumps({"number": 9, "title": "Lock fails on Linux", "state": "CLOSED",
                               "labels": [{"name": "bug"}], "body": "flock missing", "comments": []})
        if args[0] == "api" and "/commits" in a:
            return json.dumps([[{"sha": "abcdef1234567", "commit": {"message": "Add leash\n\nWorker leash body",
                                                                      "author": {"name": "kev", "date": "2026-09-21"}}}]])
        if args[:2] == ["release", "list"]:
            return json.dumps([{"tagName": "v1.0.6", "publishedAt": "2026-09-24T00:00:00Z"},
                               {"tagName": "v1.0.0", "publishedAt": "2026-01-01T00:00:00Z"}])
        if args[:2] == ["release", "view"]:
            return json.dumps({"tagName": args[2], "name": args[2], "body": "notes"})
        raise AssertionError(f"unexpected gh call {args}")


def test_export_writes_one_file_per_item_and_drops_secret_lines(tmp_path, monkeypatch):
    stub = StubGh(); monkeypatch.setattr(cg, "gh", stub)
    counts = cg.export("o/r", tmp_path, None)
    assert counts == {"prs": 1, "issues": 1, "commits": 1, "releases": 2, "dropped_lines": 1}
    pr = (tmp_path / "prs/pr-7.md").read_text()
    assert pr.startswith("# PR #7: Fix symlink crash in --add")
    assert "Resolve symlinks" in pr and "use realpath" in pr and "lgtm" in pr and "tried before" in pr
    assert FAKE_KEY not in pr
    assert "Lock fails on Linux" in (tmp_path / "issues/issue-9.md").read_text()
    assert "Worker leash body" in (tmp_path / "commits/commit-abcdef1.md").read_text()
    assert (tmp_path / "releases/release-v1.0.6.md").is_file()


def test_refresh_window_passes_since_everywhere(tmp_path, monkeypatch):
    stub = StubGh(); monkeypatch.setattr(cg, "gh", stub)
    counts = cg.export("o/r", tmp_path, "2026-09-23T00:00:00Z")
    joined = [" ".join(c) for c in stub.calls]
    assert any(c.startswith("pr list") and "updated:>=2026-09-23T00:00:00Z" in c for c in joined)
    assert any(c.startswith("issue list") and "updated:>=2026-09-23T00:00:00Z" in c for c in joined)
    assert any("commits?per_page=100&since=2026-09-23T00:00:00Z" in c for c in joined)
    assert counts["releases"] == 1  # the old release is skipped


def test_main_no_connect_records_sync_and_refresh_uses_it(tmp_path, monkeypatch):
    stub = StubGh(); monkeypatch.setattr(cg, "gh", stub)
    assert cg.main(["o/r", "--pointer", "p", "--principal", "x", "--out", str(tmp_path), "--no-connect"]) == 0
    sync = json.loads((tmp_path / cg.SYNC_FILE).read_text())
    assert sync["repo"] == "o/r" and sync["lastExport"]
    stub.calls.clear()
    assert cg.main(["o/r", "--pointer", "p", "--principal", "x", "--out", str(tmp_path),
                    "--no-connect", "--refresh"]) == 0
    assert any(f"updated:>={sync['lastExport']}" in " ".join(c) for c in stub.calls)


def test_main_connects_through_prepare_bulk(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "gh", StubGh())
    seen = {}

    class R:
        returncode = 0

    def fake_run(cmd, cwd=None):
        seen["cmd"] = cmd
        return R()
    monkeypatch.setattr(cg.subprocess, "run", fake_run)
    assert cg.main(["o/r", "--pointer", "gh-p", "--principal", "bot", "--out", str(tmp_path),
                    "--writer", "builtin"]) == 0
    cmd = seen["cmd"]
    assert cmd[1].endswith("prepare_bulk.py")
    assert cmd[cmd.index("--root") + 1] == str(tmp_path)
    assert cmd[cmd.index("--pointer") + 1] == "gh-p" and cmd[cmd.index("--principal") + 1] == "bot"
    assert cmd[cmd.index("--writer") + 1] == "builtin"


def test_bad_repo_and_gh_failure(tmp_path, monkeypatch):
    assert cg.main(["not a repo", "--pointer", "p", "--principal", "x", "--out", str(tmp_path)]) == 2

    def boom(args):
        raise RuntimeError("gh pr list failed: auth")
    monkeypatch.setattr(cg, "gh", boom)
    assert cg.main(["o/r", "--pointer", "p", "--principal", "x", "--out", str(tmp_path), "--no-connect"]) == 1
    assert not (tmp_path / cg.SYNC_FILE).exists()


def test_superjev_cli_routes_connect_github(monkeypatch):
    import superjev
    got = {}
    monkeypatch.setattr(cg, "main", lambda argv: got.setdefault("argv", argv) and 0)
    superjev.main(["connect-github", "o/r", "--pointer", "p", "--principal", "x"])
    assert got["argv"] == ["o/r", "--pointer", "p", "--principal", "x"]
