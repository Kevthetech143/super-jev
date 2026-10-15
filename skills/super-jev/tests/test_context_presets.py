#!/usr/bin/env python3
"""The context presets (Small, Medium, Large): the rows in judge_profiles.json and the loader that applies them.

Small is the profile as written. Medium and Large are `contexts` rows that override only the label, the
window, the characters read per note and the content-check timeout; the input cap and the call budget derive
from the row's window by the rules the profile already has. The request header that opts into a window above
the judge's normal limit is profile data too. No Jev call: only the loader and the table are read.
The Node twin of these tests is test/context-presets.test.ts.

    python3 -m pytest skills/super-jev/tests/test_context_presets.py -q
"""
import dataclasses
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
REPO = SKILL.parent.parent
sys.path.insert(0, str(SKILL))
import judge_profile  # noqa: E402

TABLE = SKILL / "judge_profiles.json"
NODE = shutil.which("node") or "node"

#: preset: (shown name, window, input cap, call budget, characters read per note, content-check timeout in ms)
PRESETS = {
    "small": ("Small", 32_768, 32_000, 30_000, 14_000, 15_000),
    "medium": ("Medium", 131_072, 130_304, 128_304, 56_000, 20_000),
    "large": ("Large", 262_144, 261_376, 259_376, 112_000, 30_000),
}
#: what the number of characters read costs on the wire (title prefix, per-question overhead), measured locally
WIRE = 1.45
#: the only things a contexts row may change; every other number a preset reports is the profile's own
ROW_FIELDS = {"context", "context_label", "window_tokens", "read_chars", "nav_timeout_ms"}


def _raw():
    return json.loads(TABLE.read_text())["profiles"]


def _presets():
    """(profile, preset) for every preset of every profile that has any."""
    return [(key, ctx) for key, p in _raw().items() if "contexts" in p for ctx in ["small", *p["contexts"]]]


#: the preset keys of the Jev profile as the table states them; seeds every edited copy below, so a
#: variant is wrong in exactly the one way its edit makes it
GOOD = {
    "default_context": "small", "read_chars": 14_000, "nav_timeout_ms": 15_000,
    "long_context_header": ["TypeSafe-Danger-Zone-Long-Context", "true"], "long_context_above_tokens": 65_536,
    "contexts": {
        "medium": {"label": "Medium", "window_tokens": 131_072, "read_chars": 56_000, "nav_timeout_ms": 20_000},
        "large": {"label": "Large", "window_tokens": 262_144, "read_chars": 112_000, "nav_timeout_ms": 30_000},
    },
}


def _edited(tmp_path, edit):
    """A copy of the real table, the Jev profile given the GOOD preset keys and then `edit`; returns its path."""
    data = json.loads(TABLE.read_text())
    data["profiles"]["typesafe-jev"].update(json.loads(json.dumps(GOOD)))
    edit(data["profiles"]["typesafe-jev"])
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data))
    return path


def test_small_is_the_base_profile():
    base = judge_profile.load("typesafe-jev")
    small = judge_profile.load("typesafe-jev", context="small")
    assert small == base                                    # the same profile, nothing overridden
    assert (small.context, small.context_label) == ("small", "Small")
    assert (small.window_tokens, small.input_cap_tokens, small.call_tokens) == (32_768, 32_000, 30_000)
    assert (small.read_chars, small.nav_timeout_ms) == (14_000, 15_000)
    assert "small" not in _raw()["typesafe-jev"]["contexts"]    # no duplicate row: small is the profile itself
    assert judge_profile.PROFILE.context == "small"         # what every import-time constant is derived from


@pytest.mark.parametrize("name", ["medium", "large"])
def test_medium_and_large_derive_window_input_cap_and_call_budget(name):
    label, window, cap, call, read, nav = PRESETS[name]
    base = judge_profile.load("typesafe-jev")
    p = judge_profile.load("typesafe-jev", context=name)
    assert (p.context, p.context_label) == (name, label)
    assert (p.window_tokens, p.input_cap_tokens, p.call_tokens) == (window, cap, call)
    assert (p.read_chars, p.nav_timeout_ms) == (read, nav)
    assert p.ceiling_text == f"the {window:,}-token ceiling"
    assert p.input_cap_tokens == p.window_tokens - p.input_cap_headroom       # the profile's own rules
    assert p.call_tokens == p.input_cap_tokens - p.call_headroom
    assert p.base_call_tokens == base.call_tokens == 30_000                   # the header rule measures against Small
    for f in dataclasses.fields(p):                                           # nothing else moves
        if f.name not in ROW_FIELDS:
            assert getattr(p, f.name) == getattr(base, f.name), f.name


def test_default_context_is_small():
    assert _raw()["typesafe-jev"]["default_context"] == "small"
    assert judge_profile.PROFILE.default_context == "small"
    assert judge_profile.load().context == "small"          # no preset named: the profile as written
    assert judge_profile.load("laya").default_context == "small"    # a profile with no presets says nothing: small


def test_a_default_other_than_small_does_not_move_the_profile_everything_imports(tmp_path):
    """The factory default is for the setting to read; load() with no preset stays Small, so routing, zoom,
    the gate and every other import-time constant keep Small's numbers whatever the default says."""
    p = judge_profile.load("typesafe-jev", path=_edited(tmp_path, lambda p: p.update(default_context="large")))
    assert (p.default_context, p.context, p.window_tokens, p.read_chars) == ("large", "small", 32_768, 14_000)
    assert judge_profile.load("typesafe-jev", path=_edited(tmp_path, lambda p: None)).default_context == "small"


@pytest.mark.parametrize("bad", ["xl", "Small", "LARGE", " large", "medium ", "", "constructor"])
def test_unknown_context_stops_with_one_line_naming_the_three(bad):
    with pytest.raises(ValueError) as e:
        judge_profile.load("typesafe-jev", context=bad)
    assert str(e.value) == f'unknown context "{bad}" (choose small, medium or large)'
    assert "\n" not in str(e.value)


def test_a_profile_without_contexts_accepts_only_small():
    laya = judge_profile.load("laya", context="small")
    assert laya == judge_profile.load("laya")
    assert (laya.context, laya.context_label, laya.window_tokens, laya.read_chars) == ("small", "Small", 1024, 14_000)
    assert laya.long_context_header is None and laya.long_context_above_tokens is None
    for name in ("medium", "large"):
        with pytest.raises(ValueError) as e:
            judge_profile.load("laya", context=name)
        assert str(e.value) == f'unknown context "{name}" (choose small)'


def test_every_preset_read_fits_its_call_room():
    walked = _presets()
    assert [ctx for key, ctx in walked if key == "typesafe-jev"] == ["small", "medium", "large"]
    assert not [key for key, _ in walked if key == "laya"]       # a profile with no presets has none to walk
    for key, ctx in walked:
        p = judge_profile.load(key, context=ctx)
        assert p.read_chars * WIRE <= p.call_tokens * p.bytes_per_token, (key, ctx)    # what is read goes out in one call
        assert p.read_chars <= p.file_ceiling_bytes, (key, ctx)                         # and is never more than a note can be


def _code_files():
    """The files test_judge_door.py::test_e reads: all the code, except tests and the profile itself."""
    files = (list((REPO / "src").rglob("*.ts")) + list((REPO / "bench").rglob("*.ts")) + list(SKILL.rglob("*.py"))
             + list(SKILL.glob("*.json")) + list((SKILL / "hooks").rglob("*")))
    return [f for f in files if f.is_file() and f != TABLE and SKILL / "tests" not in f.parents
            and "node_modules" not in f.parts]


def test_header_name_and_value_live_only_in_the_profile():
    large = judge_profile.load("typesafe-jev", context="large")
    name, value = large.long_context_header
    assert (name, value) == ("TypeSafe-Danger-Zone-Long-Context", "true")
    assert large.long_context_above_tokens == 65_536
    assert _raw()["typesafe-jev"]["long_context_header"] == [name, value]
    for preset in PRESETS:                                  # the rule is the profile's, not the preset's
        p = judge_profile.load("typesafe-jev", context=preset)
        assert (p.long_context_header, p.long_context_above_tokens) == ((name, value), 65_536), preset
    files = _code_files()
    assert len(files) > 30
    hits = []
    for f in files:
        try:
            if name in f.read_text():
                hits.append(str(f.relative_to(REPO)))
        except (UnicodeDecodeError, OSError):
            continue
    assert not hits, hits


BROKEN = {
    "a small row": lambda p: p["contexts"].update(small={"read_chars": 1}),
    "a row key a row may not override": lambda p: p["contexts"]["medium"].update(max_parts=5),
    "a row number that is not a number": lambda p: p["contexts"]["large"].update(read_chars="lots"),
    "a default that names no preset": lambda p: p.update(default_context="huge"),
    "a header that is not a name and a value": lambda p: p.update(long_context_header=["only-one"]),
    "a header with no threshold": lambda p: p.pop("long_context_above_tokens"),
    "no read_chars": lambda p: p.pop("read_chars"),
    "no nav_timeout_ms": lambda p: p.pop("nav_timeout_ms"),
}


@pytest.mark.parametrize("what", sorted(BROKEN))
def test_a_broken_presets_table_stops_with_one_line(what, tmp_path):
    path = _edited(tmp_path, BROKEN[what])
    with pytest.raises(SystemExit) as e:
        judge_profile.load("typesafe-jev", path=path)
    msg = str(e.value)
    assert "judge limits table" in msg and "profiles.json" in msg and "\n" not in msg.strip()


def _camel(name):
    return re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name)


def test_python_and_node_loaders_agree_on_every_preset():
    cases = [(key, ctx) for key in _raw() for ctx in ["small", *_raw()[key].get("contexts", {})]]
    assert ("typesafe-jev", "large") in cases, "the table has no presets to compare"
    script = ("import { loadJudgeProfile } from './src/judge-profile.ts';"
              f"const cases = {json.dumps(cases)};"
              "console.log(JSON.stringify(cases.map(([key, ctx]) => loadJudgeProfile(key, undefined, {}, ctx))));")
    assert shutil.which(NODE), f"node is missing: {NODE}"
    run = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=REPO, capture_output=True, text=True,
                         timeout=120)
    assert run.returncode == 0, run.stderr
    node = json.loads(run.stdout)
    assert len(node) == len(cases)
    for (key, ctx), got in zip(cases, node):
        want = judge_profile.load(key, context=ctx)
        fields = json.loads(json.dumps(dataclasses.asdict(want)))
        fields.update(input_cap_tokens=want.input_cap_tokens, call_tokens=want.call_tokens)
        for field, value in fields.items():
            assert got[_camel(field)] == value, (key, ctx, field)
