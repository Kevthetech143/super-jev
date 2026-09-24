import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
LEASH_PY = SKILL_DIR / "leash.py"

spec = importlib.util.spec_from_file_location("leash", LEASH_PY)
leash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leash)


def test_lead_call_has_no_agent_id():
    assert leash.is_subagent_call({"agent_id": None}) is False
    assert leash.is_subagent_call({}) is False


def test_subagent_call_detected_by_agent_id():
    assert leash.is_subagent_call({"agent_id": "abc123"}) is True


def test_subagent_call_detected_by_agent_type_fallback():
    assert leash.is_subagent_call({"agent_type": "worker"}) is True


def test_lead_write_always_allowed(tmp_path):
    data = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "x.txt")}}
    action, reason = leash.decide(data, {})
    assert action == "allow"
    assert "lead" in reason


def test_subagent_write_in_allowlist_allowed(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    target = allowed_dir / "note.txt"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": str(target)}}
    action, reason = leash.decide(data, config)
    assert action == "allow"


def test_subagent_write_outside_allowlist_denied(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside = tmp_path / "outside" / "note.txt"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": str(outside)}}
    action, reason = leash.decide(data, config)
    assert action == "deny"
    assert "not in worker allow-list" in reason


def test_subagent_agent_specific_allowlist_key(tmp_path):
    allowed_dir = tmp_path / "worker-b-dir"
    allowed_dir.mkdir()
    target = allowed_dir / "f.txt"
    config = {"worker-b": [str(allowed_dir)], "default": []}
    data = {"agent_id": "worker-b", "tool_name": "Write", "tool_input": {"file_path": str(target)}}
    action, reason = leash.decide(data, config)
    assert action == "allow"


def test_always_deny_beats_allowlist(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    env_target = allowed_dir / ".env"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": str(env_target)}}
    action, reason = leash.decide(data, config)
    assert action == "deny"
    assert "always-deny" in reason


def test_secret_glob_denied(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    target = allowed_dir / "api-secret-key.md"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": str(target)}}
    action, reason = leash.decide(data, config)
    assert action == "deny"


def test_reply_outbox_example_path_denied(tmp_path):
    config = {"default": ["/tmp/ai-wrapper"], "always_deny": ["/tmp/ai-wrapper"]}
    data = {
        "agent_id": "a1",
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/ai-wrapper/answer-x.txt"},
    }
    action, reason = leash.decide(data, config)
    assert action == "deny"


def test_no_path_in_tool_input_allows(tmp_path):
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {}}
    action, reason = leash.decide(data, {})
    assert action == "allow"


def test_uncoverd_tool_allows(tmp_path):
    data = {"agent_id": "a1", "tool_name": "Read", "tool_input": {"file_path": "/x"}}
    action, reason = leash.decide(data, {})
    assert action == "allow"


# --- Bash best-effort parsing ---

def test_bash_redirect_target_extracted():
    targets = leash.extract_bash_write_targets("echo hi > /tmp/out.txt")
    assert "/tmp/out.txt" in targets


def test_bash_append_redirect_target_extracted():
    targets = leash.extract_bash_write_targets("echo hi >> /tmp/out.txt")
    assert "/tmp/out.txt" in targets


def test_bash_tee_target_extracted():
    targets = leash.extract_bash_write_targets("echo hi | tee /tmp/out.txt")
    assert "/tmp/out.txt" in targets


def test_bash_no_write_target_found_for_plain_read():
    targets = leash.extract_bash_write_targets("cat /tmp/in.txt")
    assert targets == []


def test_subagent_bash_denied_outside_allowlist(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside = tmp_path / "outside.txt"
    config = {"default": [str(allowed_dir)]}
    data = {
        "agent_id": "a1",
        "tool_name": "Bash",
        "tool_input": {"command": f"echo hi > {outside}"},
    }
    action, reason = leash.decide(data, config)
    assert action == "deny"
    assert "best-effort" in reason


def test_subagent_bash_allowed_inside_allowlist(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    target = allowed_dir / "out.txt"
    config = {"default": [str(allowed_dir)]}
    data = {
        "agent_id": "a1",
        "tool_name": "Bash",
        "tool_input": {"command": f"echo hi > {target}"},
    }
    action, reason = leash.decide(data, config)
    assert action == "allow"


def test_symlinked_target_resolved_before_check(tmp_path):
    real_outside = tmp_path / "outside"
    real_outside.mkdir()
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    link = allowed_dir / "escape"
    link.symlink_to(real_outside)
    target = link / "note.txt"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": str(target)}}
    action, reason = leash.decide(data, config)
    assert action == "deny"


def test_case_insensitive_match_on_macos_style_path(tmp_path):
    allowed_dir = tmp_path / "Allowed"
    allowed_dir.mkdir()
    target_upper = str(allowed_dir).upper() + "/NOTE.TXT"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": target_upper}}
    action, reason = leash.decide(data, config)
    assert action == "allow"


def test_dotdot_traversal_resolved(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    traversal = f"{allowed_dir}/../outside.txt"
    config = {"default": [str(allowed_dir)]}
    data = {"agent_id": "a1", "tool_name": "Write", "tool_input": {"file_path": traversal}}
    action, reason = leash.decide(data, config)
    assert action == "deny"


def test_lead_bash_never_checked():
    data = {"tool_name": "Bash", "tool_input": {"command": "echo hi > /tmp/ai-wrapper/x.txt"}}
    action, reason = leash.decide(data, {"always_deny": ["/tmp/ai-wrapper"]})
    assert action == "allow"
    assert "lead" in reason


# --- error path / fail-open ---

def test_main_fails_open_on_bad_json(tmp_path):
    result = subprocess.run(
        [sys.executable, str(LEASH_PY)],
        input="not json{{{",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_main_allows_empty_stdin():
    result = subprocess.run(
        [sys.executable, str(LEASH_PY)],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_main_denies_subagent_write_via_cli(tmp_path):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    outside = tmp_path / "outside.txt"
    config = {"default": [str(allowed_dir)]}
    config_path = tmp_path / "leash.json"
    config_path.write_text(json.dumps(config))
    payload = json.dumps(
        {
            "agent_id": "a1",
            "tool_name": "Write",
            "tool_input": {"file_path": str(outside)},
        }
    )
    result = subprocess.run(
        [sys.executable, str(LEASH_PY)],
        input=payload,
        capture_output=True,
        text=True,
        env={"LEASH_CONFIG": str(config_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 2
    assert "not in worker allow-list" in result.stderr


def test_main_allows_lead_write_via_cli(tmp_path):
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/tmp/ai-wrapper/x.txt"}})
    result = subprocess.run(
        [sys.executable, str(LEASH_PY)],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0


# --- adversarial review additions ---

def _bash(cmd, allowed, cwd=None):
    data = {"agent_id": "a1", "tool_name": "Bash", "tool_input": {"command": cmd}}
    if cwd:
        data["cwd"] = str(cwd)
    return leash.decide(data, {"default": [str(allowed)], "always_deny": [".env"]})


def test_bash_dev_null_redirect_not_blocked(tmp_path):
    # False positive: every `2>/dev/null` from a worker was denied.
    assert _bash("ls missing 2>/dev/null", tmp_path)[0] == "allow"
    assert _bash("echo hi >/dev/stderr", tmp_path)[0] == "allow"


def test_bash_quoted_target_outside_allowlist_denied(tmp_path):
    # Bypass: quotes stayed in the target, so it resolved under the (allowed) cwd.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside" / "f.txt"
    action, _ = _bash(f"echo x > '{outside}'", allowed, cwd=allowed)
    assert action == "deny"
    action, _ = _bash(f'echo x > "{outside}"', allowed, cwd=allowed)
    assert action == "deny"


def test_bash_relative_target_resolved_against_payload_cwd(tmp_path, monkeypatch):
    # Relative targets must resolve against the agent's cwd from the payload,
    # not wherever the hook process happens to run.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(allowed)
    assert _bash("echo x > f.txt", allowed, cwd=other)[0] == "deny"
    monkeypatch.chdir(other)
    assert _bash("echo x > f.txt", allowed, cwd=allowed)[0] == "allow"


def test_multiedit_outside_allowlist_denied(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    data = {"agent_id": "a1", "tool_name": "MultiEdit",
            "tool_input": {"file_path": str(tmp_path / "outside.py"), "edits": []}}
    assert leash.decide(data, {"default": [str(allowed)]})[0] == "deny"
