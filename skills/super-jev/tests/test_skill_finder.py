"""Skill finder promise (frozen tests, offline, no Jev):
1. the released copy runs only its own runtime (never a home-directory checkout);
2. skill roots are chosen in one place (search.sh), not also in dispatch.py, and follow the
   agent that asks: Claude Code searches ~/.claude/skills, any other agent its own roots.json;
3. a real runtime/launcher failure is an error outcome carrying the real cause;
4. local fallback guesses are labelled as guesses, never as a catalog match.

Run:  python3 -m pytest skills/super-jev/tests/test_skill_finder.py -q
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / "super-jev"))
import ask  # noqa: E402

FAKE_NODE = """#!/bin/sh
# fake node: $1 = runtime entry; logs which entry ran and the roots it was given
echo "$1" >> "$FAKE_LOG/entries"
prev=""
for a in "$@"; do
  [ "$prev" = "--roots-file" ] && cp "$a" "$FAKE_LOG/roots.json"
  prev="$a"
done
case "${FAKE_MODE:-ok}" in
  ok) echo '{"status":"suggestions","source":"jev","candidates":[{"id":"a","name":"a","path":"/x/a/SKILL.md","description":"d"}]}' ;;
  fail) echo "The request file exceeds 20000 bytes; split it into smaller runs" >&2
        echo "second line of noise" >&2
        exit 1 ;;
esac
"""


@pytest.fixture
def release(tmp_path):
    """A release-shaped tree: <rel>/skills/{super-jev,skill-search} and <rel>/src."""
    rel = tmp_path / "release"
    (rel / "skills").mkdir(parents=True)
    (rel / "src").mkdir()
    (rel / "src/skill-search-cli.ts").write_text("// fake runtime\n")
    shutil.copytree(SKILLS / "skill-search", rel / "skills/skill-search")
    (rel / "skills/super-jev").mkdir()
    shutil.copyfile(SKILLS / "super-jev/dispatch.py", rel / "skills/super-jev/dispatch.py")
    (rel / "skills/skill-search/roots.json").write_text('["~/.codex/skills"]')
    node = tmp_path / "fakebin/node"
    node.parent.mkdir()
    node.write_text(FAKE_NODE)
    node.chmod(0o755)
    home = tmp_path / "home"
    decoy = home / "super-jev/src"
    decoy.mkdir(parents=True)
    (decoy / "skill-search-cli.ts").write_text("// decoy main checkout\n")
    log = tmp_path / "log"
    log.mkdir()
    env = {**os.environ, "HOME": str(home), "SKILL_SEARCH_NODE_BIN": str(node), "FAKE_LOG": str(log)}
    # CLAUDECODE is set in every shell Claude Code spawns, so a contributor running pytest there has it
    for k in ("CLAUDECODE", "CLAW4MAC_AI_MODEL", "SKILL_SEARCH_AI_MODEL", "FAKE_MODE",
              "SKILL_SEARCH_PROVIDER_CMD"):
        env.pop(k, None)
    return rel, env, log


def dispatch(rel, env, *args):
    r = subprocess.run([sys.executable, str(rel / "skills/super-jev/dispatch.py"), "skills", *args],
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


def one_json(out):
    lines = out.strip().splitlines()
    assert len(lines) == 1, out
    return json.loads(lines[0])


def test_release_runs_only_its_own_runtime_never_a_home_checkout(release):
    rel, env, log = release
    rc, out, _ = dispatch(rel, env, "--local-only", "--request", "print a label")
    assert rc == 0 and one_json(out)["status"] == "suggestions"
    entries = (log / "entries").read_text().split()
    assert entries == [str(rel / "src/skill-search-cli.ts")]


def test_roots_are_chosen_in_search_sh_only(release):
    rel, env, log = release
    (rel / "skills/skill-search/roots-claude.json").write_text('["~/.claude/skills"]')
    # dispatch passes no roots of its own, whatever the install folder is called
    stub = rel / "skills/skill-search/search.sh"
    real = stub.read_text()
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_LOG/args"\n')
    dispatch(rel, env, "--request", "x")
    assert "--config" not in (log / "args").read_text().split()
    stub.write_text(real)
    # search.sh picks the Claude roots when it runs inside Claude Code
    rc, out, _ = dispatch(rel, {**env, "CLAUDECODE": "1"}, "--local-only", "--request", "x")
    assert rc == 0
    assert json.loads((log / "roots.json").read_text()) == [str(Path(env["HOME"]) / ".claude/skills")]
    rc, out, _ = dispatch(rel, env, "--local-only", "--request", "x")
    assert json.loads((log / "roots.json").read_text()) == [str(Path(env["HOME"]) / ".codex/skills")]


def test_claude_code_searches_its_own_skill_folder_with_no_roots_file(release):
    """A new user's Claude Code has no roots.json (it is never shipped) and still gets a search."""
    rel, env, log = release
    (rel / "skills/skill-search/roots.json").unlink()
    rc, out, _ = dispatch(rel, {**env, "CLAUDECODE": "1"}, "--local-only", "--request", "x")
    assert rc == 0, out
    assert json.loads((log / "roots.json").read_text()) == [str(Path(env["HOME"]) / ".claude/skills")]


def test_another_agent_with_no_roots_file_gets_one_setup_line(release):
    rel, env, log = release
    (rel / "skills/skill-search/roots.json").unlink()
    rc, out, _ = dispatch(rel, env, "--local-only", "--request", "x")
    body = one_json(out)
    assert rc == 2 and body["status"] == "error" and body["candidates"] == []
    assert "roots.example.json" in body["error"] and "roots.json" in body["error"]
    assert not (log / "entries").exists()  # the runtime never ran


def test_an_explicit_missing_config_keeps_the_plain_message(release):
    rel, env, _ = release
    rc, out, _ = dispatch(rel, env, "--local-only", "--config", str(rel / "nope.json"), "--request", "x")
    body = one_json(out)
    assert rc == 2 and "roots source not readable" in body["error"] and "roots.example.json" not in body["error"]


def test_a_fleet_model_variable_no_longer_picks_the_roots(release):
    """The old rule keyed on an app-specific variable; one agent's own roots.json now wins outside Claude Code."""
    rel, env, log = release
    (rel / "skills/skill-search/roots-claude.json").write_text('["~/.claude/skills"]')
    for var in ("CLAW4MAC_AI_MODEL", "SKILL_SEARCH_AI_MODEL"):
        rc, out, _ = dispatch(rel, {**env, var: "claude-x"}, "--local-only", "--request", "x")
        assert rc == 0, out
        assert json.loads((log / "roots.json").read_text()) == [str(Path(env["HOME"]) / ".codex/skills")]


def test_the_machines_own_roots_file_and_key_provider_are_never_tracked():
    repo = SKILLS.parent
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=repo, capture_output=True).returncode:
        pytest.skip("not a git checkout")
    for path in ("skills/skill-search/roots.json", "skills/skill-search/deploy/local-key-provider.py"):
        r = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=repo)
        assert r.returncode == 0, f"{path} is not gitignored"


def test_no_key_and_no_provider_says_which_key_and_that_no_provider_is_set(release):
    """Through search.sh (the real door): it used to point the wrapper at a provider file nobody ships."""
    rel, env, _ = release
    (rel / "skills/skill-search/deploy/local-key-provider.py").unlink(missing_ok=True)
    rc, out, _ = dispatch(rel, {**env, "TYPESAFE_API_KEY": ""}, "--request", "x")
    body = one_json(out)
    assert rc == 2 and body["status"] == "error"
    assert "TYPESAFE_API_KEY" in body["error"] and "credential provider" in body["error"]
    assert "failed" not in body["error"] and "--local-only" in body["error"]


def test_a_provider_the_machine_ships_is_still_used(release):
    rel, env, log = release
    provider = rel / "skills/skill-search/deploy/local-key-provider.py"
    provider.write_text("#!/bin/sh\necho FAKE-KEY-FROM-PROVIDER\n")
    provider.chmod(0o755)
    rc, out, _ = dispatch(rel, {**env, "TYPESAFE_API_KEY": ""}, "--request", "x")
    assert rc == 0 and one_json(out)["status"] == "suggestions"


def test_runtime_failure_is_an_error_with_the_real_cause(release):
    rel, env, _ = release
    rc, out, _ = dispatch(rel, {**env, "FAKE_MODE": "fail"}, "--local-only", "--request", "x")
    body = one_json(out)
    assert rc != 0 and body["status"] == "error" and body["candidates"] == []
    assert "exceeds 20000 bytes" in body["error"]
    assert "local catalog" not in body["error"] and "noise" not in body["error"]


def test_missing_runtime_is_an_error_not_a_fallback(release):
    rel, env, _ = release
    (rel / "src/skill-search-cli.ts").unlink()
    rc, out, _ = dispatch(rel, env, "--local-only", "--request", "x")
    body = one_json(out)
    assert rc != 0 and body["status"] == "error" and "not installed" in body["error"]


def test_no_credential_provider_is_an_error_naming_the_cause(release):
    rel, env, _ = release
    env = {**env, "TYPESAFE_API_KEY": ""}
    env.pop("SKILL_SEARCH_PROVIDER_CMD", None)
    hook = rel / "skills/skill-search/deploy/hook-wrapper.sh"
    r = subprocess.run(["bash", str(hook), "--request-file", "r.json"], capture_output=True, text=True, env=env)
    body = one_json(r.stdout)
    assert r.returncode != 0 and body["status"] == "error" and "credential" in body["error"]


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_request_is_an_error_that_does_not_name_a_temp_file(release, value):
    rel, env, _ = release
    rc, out, _ = dispatch(rel, env, "--local-only", "--request", value)
    body = one_json(out)
    assert rc != 0 and body["status"] == "error"
    assert "request" in body["error"] and "/tmp" not in out and "/var/" not in out


def cat_reply(monkeypatch, body):
    class R:
        stdout, returncode = json.dumps(body), 0
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    return ask.skill_catalog("which skill prints a label")


CAND = [{"name": "a", "path": "/x/a/SKILL.md"}]


@pytest.mark.parametrize("body,guess", [
    ({"status": "exact", "source": "local", "candidates": CAND}, False),
    ({"status": "suggestions", "source": "jev", "candidates": CAND}, False),
    ({"status": "suggestions", "source": "local", "candidates": CAND}, True),
    ({"status": "fallback", "source": "local", "candidates": CAND}, True),
    ({"status": "clarify", "source": "jev", "candidates": CAND}, True),
])
def test_only_confirmed_results_are_called_a_match(monkeypatch, body, guess):
    (name, path), = cat_reply(monkeypatch, body)
    assert path == "/x/a/SKILL.md"
    assert (ask.GUESS in name) is guess
    note = ask.skill_note(name)
    assert ("unverified" in note) is guess
    assert ("match" in note) is (not guess)


def test_an_error_reply_gives_no_skills(monkeypatch):
    assert cat_reply(monkeypatch, {"status": "error", "candidates": [], "error": "boom"}) == []


def test_symlinked_skill_folder_still_runs_its_own_release(release, tmp_path):
    """sync.sh layout: ~/.claude/skills/skill-search -> <release>/skills/skill-search."""
    rel, env, log = release
    skills = tmp_path / "home/.claude/skills"
    skills.mkdir(parents=True)
    (skills / "skill-search").symlink_to(rel / "skills/skill-search")
    (rel / "req.json").write_text('{"request":"x"}')
    r = subprocess.run(["bash", str(skills / "skill-search/search.sh"), "--local-only", "--request-file",
                        str(rel / "req.json")], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout
    assert (log / "entries").read_text().split() == [str(rel.resolve() / "src/skill-search-cli.ts")]


def test_an_error_reply_prints_one_cause_line(monkeypatch, capsys):
    cat_reply(monkeypatch, {"status": "error", "candidates": [], "error": "boom cause"})
    assert "boom cause" in capsys.readouterr().err
