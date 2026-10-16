#!/usr/bin/env python3
"""Large is not offered in this release (context setting 8/8).

The paid A/B of Medium is done: Medium is offered, Small stays the default, and Large is NOT offered until it has its own A/B.
So the judge profile this build ships (judge_profiles.json) states Small and Medium only, from both loaders; asking for Large,
on one ask or as the default setting, says it is not offered yet and is refused the way a name that is no size is refused (exit 2,
one line, nothing searched, the sizes that are offered); and the pages say Large comes after its own test. What stays: the
long-context header code (Medium's window is above long_context_above_tokens, so a call Small cannot carry still carries the
header), and every loader and header rule at a size above Medium, which the made-up table below keeps tested for the day Large
has its own test.

  - The shipped table, the Python loader and (through a subprocess) the Node loader and navigation-cli: small and medium.
  - `--context large`, `--set-context large`, SUPERJEV_CONTEXT=large and a saved `large`: "not offered yet", exit 2.
  - A made-up table WITH a `large` row (the large_row fixture in conftest.py) still loads in both loaders, and a call above
    long_context_above_tokens still carries the header, at Medium of the shipped table and at the made-up Large.
  - SKILL.md, references/source-retrieval.md, docs/jev-cli.md and the changelog entry say Large comes after its own test.

The tests that prove the refusals and the pages fail on the build before this change, which offered Large. No Jev call, no
network, no key.

    python3 -m pytest skills/super-jev/tests/test_large_not_offered.py -q
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parents[1]
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location("ask_large_not_offered", SKILL / "ask.py")
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)
judge_profile = ask.judge_profile
context_setting = ask.context_setting

SHIPPED = SKILL / "judge_profiles.json"
NODE = shutil.which("node") or "node"
QUESTION = "when is the zeppelin quokka inspection scheduled"
#: What asking for Large says, from the flag; from the variable or the saved choice it names where the name was read.
NOT_YET = 'ask: context "large" is not offered yet (choose small or medium)\n'


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """No staged zoom picks, no claim, the preset small, and a state folder of its own."""
    monkeypatch.setattr(ask, "_STAGE", {})
    monkeypatch.setitem(ask._CLAIM, "text", None)
    monkeypatch.setattr(ask, "_CONTEXT", {"name": "small", "source": "default", "small_paths": set()})
    monkeypatch.setenv("SUPERJEV_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SUPERJEV_AUTO_CACHE", "0")


def run(monkeypatch, capsys, *argv):
    """One ask.py run for principal "me": (exit code, what it printed)."""
    monkeypatch.setattr(sys, "argv", ["ask.py", "--principal", "me", *argv])
    rc = ask.main()
    return rc, capsys.readouterr().out


def spy(monkeypatch):
    """ask.lookup stands in for the whole search: it records the question, the size and where the size came from."""
    seen = []

    def lookup(question, principal, sdir):
        seen.append((question, ask._CONTEXT["name"], ask._CONTEXT.get("source")))
        return ask._done("found", "1 file")
    monkeypatch.setattr(ask, "lookup", lookup)
    return seen


def settings(tmp_path):
    return tmp_path / "state" / "me" / "settings.json"


def node_json(script, **kw):
    """An ES-module script run by node from the repo root; what it prints is one JSON value."""
    assert shutil.which(NODE), f"node is missing: {NODE}"
    done = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=REPO, capture_output=True, text=True, timeout=120, **kw)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ---------------------------------------------------------------- the shipped profile offers Small and Medium
def test_the_shipped_jev_profile_states_exactly_small_and_medium():
    table = json.loads(SHIPPED.read_text())["profiles"]["typesafe-jev"]
    assert list(table["contexts"]) == ["medium"]  # small is the profile itself, so no row: and no large row
    assert table["default_context"] == "small"  # the default stays Small
    for name, label, window in (("small", "Small", 32_768), ("medium", "Medium", 131_072)):
        p = judge_profile.load("typesafe-jev", context=name)
        assert (p.context, p.context_label, p.window_tokens) == (name, label, window)
    with pytest.raises(ValueError) as e:
        judge_profile.load("typesafe-jev", context="large")  # the loader's own refusal of a preset the profile does not state
    assert str(e.value) == 'unknown context "large" (choose small or medium)'
    # the setting offers what the profile states, and no more
    assert context_setting.NAMES == ("small", "medium")
    assert context_setting.offered() == ["small", "medium"]
    assert judge_profile.PROFILE.default_context == "small"


def test_the_node_loader_offers_the_same_two_sizes():
    """The Node loader reads the same table the same way: small and medium load, large is refused in the Python loader's words."""
    script = ("import { loadJudgeProfile } from './src/judge-profile.ts';"
              "console.log(JSON.stringify(['small', 'medium', 'large'].map(ctx => {"
              " try { const p = loadJudgeProfile('typesafe-jev', undefined, {}, ctx); return [p.context, p.contextLabel, p.windowTokens]; }"
              " catch (e) { return { error: e.message }; } })));")
    assert node_json(script) == [["small", "Small", 32_768], ["medium", "Medium", 131_072],
                                 {"error": 'unknown context "large" (choose small or medium)'}]


def test_navigation_cli_refuses_large_before_any_judge_and_takes_medium():
    """The content-check CLI reads its `context` first: large is a one-line exit 2 whatever else is wrong with the input, and medium
    is accepted (the run then goes on to ask for the key, which this test does not have)."""
    catalog = {"version": 1, "structure": "flat-files", "rootId": "root", "nodes": [{"id": "root", "label": "root", "description": "root"}]}
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}

    def cli(context):
        body = json.dumps({"question": "q", "catalog": catalog, "context": context})
        return subprocess.run([NODE, "src/navigation-cli.ts"], input=body, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    large, medium = cli("large"), cli("medium")
    assert (large.returncode, large.stdout, large.stderr) == (2, "", 'unknown context "large" (choose small or medium)\n')
    assert medium.returncode == 2 and "TYPESAFE_API_KEY" in medium.stderr and "unknown context" not in medium.stderr


# ---------------------------------------------------------------- asking for Large says it is not offered yet
def test_asking_for_large_on_one_ask_is_refused_as_not_offered_yet(monkeypatch, capsys):
    seen = spy(monkeypatch)
    assert run(monkeypatch, capsys, "--context", "large", "--", QUESTION) == (2, NOT_YET)
    assert run(monkeypatch, capsys, "--context", " LARGE ", "--", QUESTION) == (2, NOT_YET)  # read as a person types it
    assert not seen  # nothing was searched
    # exactly the way a name that is no size is refused: exit 2, one `ask:` line naming the sizes that are offered, nothing searched
    assert run(monkeypatch, capsys, "--context", "xl", "--", QUESTION) == (2, 'ask: unknown context "xl" (choose small or medium)\n')
    # a program that asked for --json gets one object, not a bare line, and the same exit code
    rc, raw = run(monkeypatch, capsys, "--json", "--context", "large", "--", QUESTION)
    obj = json.loads(raw)
    assert rc == 2 and obj["v"] == 1 and obj["outcome"] == "not-supported" and NOT_YET.strip() in obj["why"]
    assert not seen
    # the sizes that are offered still work
    for size in ("small", "medium"):
        assert run(monkeypatch, capsys, "--context", size, "--", QUESTION)[0] == 0
    assert [s[1:] for s in seen] == [("small", "flag"), ("medium", "flag")]


def test_asking_for_large_as_the_default_setting_is_refused_the_same_way(tmp_path, monkeypatch, capsys):
    seen = spy(monkeypatch)
    # saving it: refused with the same line, and nothing is written
    assert run(monkeypatch, capsys, "--set-context", "large") == (2, NOT_YET)
    assert run(monkeypatch, capsys, "--set-context", " Large ") == (2, NOT_YET)
    assert not settings(tmp_path).exists()
    # the shell's choice: the line says where the name was read
    monkeypatch.setenv("SUPERJEV_CONTEXT", "large")
    assert run(monkeypatch, capsys, "--", QUESTION) == (2, 'ask: context "large" in SUPERJEV_CONTEXT is not offered yet (choose small or medium)\n')
    monkeypatch.delenv("SUPERJEV_CONTEXT")
    # a choice already saved (by hand, or by a build that offered Large) is refused too, until it is mended; it is not read as small
    settings(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    settings(tmp_path).write_text(json.dumps({"context": "large", "theme": "dark"}))
    assert run(monkeypatch, capsys, "--", QUESTION) == (2, 'ask: context "large" in settings.json is not offered yet (choose small or medium)\n')
    assert not seen
    # the one request that does not read the setting mends it, and the other keys of the file stay
    assert run(monkeypatch, capsys, "--set-context", "small")[0] == 0
    assert json.loads(settings(tmp_path).read_text()) == {"context": "small", "theme": "dark"}
    assert run(monkeypatch, capsys, "--", QUESTION)[0] == 0 and seen[-1][1:] == ("small", "settings")
    # medium can be saved
    assert run(monkeypatch, capsys, "--set-context", "medium")[0] == 0 and json.loads(settings(tmp_path).read_text())["context"] == "medium"


def test_a_real_ask_py_process_refuses_large_and_saves_medium(tmp_path):
    """Not through the test's own stand-ins: the script as a person runs it. It needs no key and no network, because the size is
    read before anything is searched."""
    env = {**os.environ, "SUPERJEV_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)}

    def ask_py(*args):
        done = subprocess.run([sys.executable, str(SKILL / "ask.py"), "--principal", "me", *args], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout, done.stderr
    assert ask_py("--context", "large", "--", QUESTION) == (2, NOT_YET, "")
    assert ask_py("--set-context", "large") == (2, NOT_YET, "")
    assert not settings(tmp_path).exists()
    rc, out, err = ask_py("--set-context", "medium")
    assert rc == 0 and out.startswith("Context for me is now Medium (saved in ") and not err
    assert json.loads(settings(tmp_path).read_text()) == {"context": "medium"}


def test_a_judge_that_states_only_small_is_refused_with_its_own_list(monkeypatch, capsys):
    """The sizes named are the judge profile's own: Laya states no size above small, so its list is just small. A size this build
    offers that the judge states no row for is unknown to that judge; Large, which no judge is offered yet, is not offered yet."""
    seen = spy(monkeypatch)
    monkeypatch.setattr(judge_profile, "PROFILE", judge_profile.load("laya"))
    assert run(monkeypatch, capsys, "--context", "medium", "--", QUESTION) == (2, 'ask: unknown context "medium" (choose small)\n')
    assert run(monkeypatch, capsys, "--context", "large", "--", QUESTION) == (2, 'ask: context "large" is not offered yet (choose small)\n')
    assert run(monkeypatch, capsys, "--context", "small", "--", QUESTION)[0] == 0
    assert [s[1:] for s in seen] == [("small", "flag")]


def test_the_help_text_and_the_usage_line_offer_only_small_and_medium(monkeypatch, capsys):
    doc = " ".join(ask.__doc__.split())
    assert "--set-context small|medium " in doc and '--context small|medium "question"' in doc
    assert "small|medium|large" not in doc and "112,000" not in doc  # no size that is not offered
    assert re.search(r"Large[^.]{0,80}after its own test", doc), "the help text does not say Large comes after its own test"
    assert run(monkeypatch, capsys, "--set-context") == (2, "usage: --principal AGENT --set-context small|medium\n")


# ---------------------------------------------------------------- a made-up table with a large row still works
def test_a_made_up_profile_with_a_large_row_still_loads(large_row):
    """The mechanism for a size above Medium is still there and still tested: the shipped table plus one `large` row loads in both
    loaders, so the day Large has its own test the change is a row and a name."""
    p = judge_profile.load("typesafe-jev", context="large")
    assert (p.context, p.context_label, p.window_tokens, p.input_cap_tokens, p.call_tokens, p.read_chars, p.nav_timeout_ms) == (
        "large", "Large", 262_144, 261_376, 259_376, 112_000, 30_000)
    script = ("import { loadJudgeProfile } from './src/judge-profile.ts';"
              f"const p = loadJudgeProfile('typesafe-jev', {json.dumps(str(large_row))}, {{}}, 'large');"
              "console.log(JSON.stringify([p.context, p.contextLabel, p.windowTokens, p.inputCapTokens, p.callTokens, p.readChars, p.navTimeoutMs]));")
    assert node_json(script) == ["large", "Large", 262_144, 261_376, 259_376, 112_000, 30_000]


def test_the_setting_picks_large_when_the_profile_states_it(large_row, monkeypatch, capsys):
    """The setting offers what the judge profile states: with a made-up row for Large, the flag, the variable and the saved choice
    take it (the refusals above are the shipped table's, not a rule against the name)."""
    seen = spy(monkeypatch)
    assert context_setting.pick("large") == "large"
    assert run(monkeypatch, capsys, "--context", "large", "--", QUESTION)[0] == 0
    monkeypatch.setenv("SUPERJEV_CONTEXT", " Large ")
    assert run(monkeypatch, capsys, "--", QUESTION)[0] == 0
    assert [s[1:] for s in seen] == [("large", "flag"), ("large", "env")]
    assert run(monkeypatch, capsys, "--set-context", "large")[0] == 0


def test_calls_above_the_long_context_limit_still_carry_the_header(large_row):
    """The long-context header code stays. Medium's window (131,072) is above the profile's long_context_above_tokens, so at the
    shipped Medium, and at a made-up Large, a request over Small's call budget carries the header the profile names; one Small can
    carry does not, and Small never does."""
    script = f"""
import {{ Jev }} from './src/jev.ts';
import {{ judgeTokens, loadJudgeProfile }} from './src/judge-profile.ts';
const made = {json.dumps(str(large_row))};
const question = {{ type: 'noul', instructions: 'Does the note say so?' }};
const prose = (tokens) => 'The cuff watch note says the wrist model is validated. '.repeat(Math.ceil((tokens * 2) / 55)).slice(0, tokens * 2);
const sized = (total) => ({{ state: prose(total - judgeTokens(question)), questions: {{ q1: question }} }});
async function headers(profile, request) {{
  const seen = [];
  const fetch = async (_url, init) => {{ seen.push(init.headers); return new Response(JSON.stringify({{ model: 'm', answers: {{ q1: {{ type: 'noul', noul: 0.5 }} }} }})); }};
  await new Jev({{ apiKey: 'k', fetch, profile }}).evaluate(request, new AbortController().signal);
  return seen[0];
}}
const out = {{}};
for (const [label, file, context] of [['small', undefined, 'small'], ['medium', undefined, 'medium'], ['large', made, 'large']]) {{
  const p = loadJudgeProfile('typesafe-jev', file, {{}}, context);
  const [name, value] = p.longContextHeader;
  const carries = async (total) => (await headers(p, sized(total)))[name] === value;
  out[label] = {{ window: p.windowTokens, above: p.longContextAboveTokens, base: p.baseCallTokens,
    over: await carries(p.baseCallTokens + 1), at: await carries(p.baseCallTokens), far: await carries(p.baseCallTokens * 4) }};
}}
console.log(JSON.stringify(out));
"""
    got = node_json(script)
    assert got["small"] == {"window": 32_768, "above": 65_536, "base": 30_000, "over": False, "at": False, "far": False}
    for size in ("medium", "large"):
        g = got[size]
        assert g["window"] > g["above"], size  # the window is past the limit the header lifts
        assert (g["over"], g["at"], g["far"]) == (True, False, True), size  # one token over Small's budget carries it; Small's budget does not
    assert got["medium"]["window"] == 131_072  # of the shipped table: Medium needs the header, and it is still sent


# ---------------------------------------------------------------- the pages say Large comes after its own test
PAGES = {"SKILL.md": SKILL / "SKILL.md", "source-retrieval.md": SKILL / "references" / "source-retrieval.md",
         "jev-cli.md": REPO / "docs" / "jev-cli.md"}


@pytest.mark.parametrize("page", PAGES)
def test_the_pages_say_large_comes_after_its_own_test(page):
    flat = " ".join(PAGES[page].read_text().split())
    assert re.search(r"Large[^.]{0,80}after its own test", flat), f"{page} does not say Large comes after its own test"
    assert "small|medium|large" not in flat.lower(), f"{page} still offers Large in a choice of sizes"
    assert not re.search(r"Large 112,000|Large \| up to 112,000|Medium 56,000 and Large", flat), f"{page} still gives Large as a size that reads"


def test_the_changelog_entry_says_large_comes_after_its_own_test():
    version = json.loads((REPO / "package.json").read_text())["version"]
    entries = re.split(r"^## ", (REPO / "CHANGELOG.md").read_text(), flags=re.M)[1:]
    heading, body = entries[0].split("\n", 1)
    assert heading.startswith(version), f"the top changelog entry is {heading!r}, package.json says {version}"  # no version bump for this
    flat = " ".join(body.split())
    assert re.search(r"Large[^.]{0,80}after its own test", flat), "the top entry does not say Large comes after its own test"
    assert "small|medium|large" not in flat.lower() and "three sizes" not in flat.lower()
