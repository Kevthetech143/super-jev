"""The real path, end to end: ask.py -> dispatch.py -> verified-pointer-memory/cli.py ->
src/navigation-cli.ts -> judge doorway -> fetch. Only fetch is replaced (an offline stub, so no key
and no network); everything else is the code that ships. It exists because a request shape the Node
side rejects ("Navigation input is invalid") passed every unit test when only pieces ran together.

The memory config must point at THIS checkout's Node: a config pinned to another checkout's
src/navigation-cli.ts is a deployment problem, not a code problem, and this test names it."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ASK = REPO / "skills" / "super-jev" / "ask.py"
EXP = REPO / "experiments" / "verified-pointer-memory"
NAV = ["node", str(REPO / "src" / "navigation-cli.ts")]
STUB = """
globalThis.fetch = async (_url, opts) => {
  const payload = JSON.parse(opts.body);
  const answers = Object.fromEntries(Object.entries(payload.questions).map(([id, q]) => {
    const keys = Object.keys(q.criteria);
    const choice = keys.find(k => k.endsWith('o_0')) || keys[0];
    return [id, {type: 'choice', choice, confidence: 0.95,
      probabilities: Object.fromEntries(keys.map(k => [k, k === choice ? 0.95 : 0.05 / (keys.length - 1 || 1)]))}];
  }));
  return new Response(JSON.stringify({model: 'offline', answers}), {status: 200});
};
"""


def test_ask_reaches_the_judge_through_node_and_never_says_navigation_input_is_invalid(tmp_path):
    if subprocess.run(["node", "--version"], capture_output=True).returncode:
        pytest.skip("node is not installed")
    note = tmp_path / "bp.md"
    note.write_text("# Blood pressure watches\nThe cuff-style watch model X is the one validated against a clinic cuff.\n")
    hook = tmp_path / "offline.cjs"
    hook.write_text(STUB)
    state = tmp_path / "state"
    (state / "_memory").mkdir(parents=True)
    config = {"db": str(tmp_path / "db.sqlite"), "registry": str(tmp_path / "registry.json"),
              "navigationCommand": NAV, "retrievalCommand": NAV, "providerTimeoutSeconds": 60,
              "cacheTtlSeconds": 60, "reviewTtlSeconds": 60}
    (state / "_memory" / "config.json").write_text(json.dumps(config))
    env = {**os.environ, "SUPERJEV_STATE_DIR": str(state), "TYPESAFE_API_KEY": "fake-not-real",
           "NODE_OPTIONS": f"--require={hook}", "SUPERJEV_AUTO_CACHE": "0", "SUPERJEV_REPO": str(REPO)}
    env.pop("SUPERJEV_JUDGE", None)
    sys.path.insert(0, str(EXP))
    from path_connect import connect  # noqa: E402
    draft = connect({"pointer": "bp", "principals": ["me"], "sources": [{"path": str(note)}]}, config)
    result = connect({"pointer": "bp", "principals": ["me"], "reviewed": True, "navigationSHA": draft["navigationSHA"],
                      "sources": [{"path": x["path"], "sha256": x["sha256"]} for x in draft["sources"]]}, config)
    assert result.get("status") == "registered", result
    out = subprocess.run([sys.executable, str(ASK), "--principal", "me", "which blood pressure watch can I trust?"],
                         capture_output=True, text=True, env=env, timeout=170, cwd=str(tmp_path))
    text = out.stdout + out.stderr
    assert "Navigation input is invalid" not in text, text
    assert "sets failed" not in text, text
    assert "OUTCOME: found" in text, text
