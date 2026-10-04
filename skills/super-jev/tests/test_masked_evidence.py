#!/usr/bin/env python3
"""Offline tests: a diff holding secret-shaped text (a card scanner's fake card numbers, a
redaction test's sample key) is judged file by file instead of refused whole. A file whose
text scans as a secret is withheld whole, real and fake values alike; every other file goes
to the judge as it is. A claim holding a secret is still refused, and jev_client.ask keeps
its own full scan.

Why whole files: the scanner finds where a secret starts, never where it ends. Four review
rounds of masking inside a file each found a part of a secret still sent (a key body, a
wrapped token, a JWT signature, the secret beside an AWS key id, a card's CVV, a value
concatenated over lines). Every such shape is kept below as a leak case.

Token-shaped strings are built by concatenation so this file itself never holds one.

    python3 -m pytest skills/super-jev/tests/test_masked_evidence.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, str(SKILL))
pb = load("prepare_bulk_mask", SKILL / "prepare_bulk.py")
jc = load("jev_client_mask", SKILL / "lib" / "jev_client.py")
sj = load("superjev_mask", SKILL / "superjev.py")

CARD = "4000 " + "0566 5566 5556"
PW = "password=" + "hunter2" + "hunter2"
STRIPE = "sk_" + "live_" + "abcdefghij1234567890"
AWS_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
AWS_SK = "wJalrXUtnFEMI/" + "K7MDENG/" + "bPxRfiCYEXAMPLEKEY"
VALUE = "Hunter2" + "Hunter2Xy9"
PWD = "pass" + "word"  # the keyword, so no line of this file scans as a secret
TOK = "TO" + "KEN"  # likewise
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----"
PEM_END = "-----END RSA " + "PRIVATE KEY-----"
KEY_BODY = ["MIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun",
            "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK",
            "LxoS2tFczGkPLPgizskuemMghRniWaoLcyehkd3qqGElvW/VDL5AaWTg0nLVkjRo"]
JWT_HEAD = "ey" + "JhbGciOiJIUzI1NiJ9"
JWT_BODY = "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0"
JWT_SIG = "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
BASIC = "dXNlcjpwYXNzd29y" + "ZDEyMzQ1Njc4OTA="

PRODUCT = ("diff --git a/src/scan.py b/src/scan.py\n--- a/src/scan.py\n+++ b/src/scan.py\n"
           "@@ -1 +1,2 @@\n def scan(text):\n+    return card_hit(text)\n")


def test_file(body, name="tests/test_scan.py"):
    """A file's diff section as git writes it: each hunk's @@ line counts its own lines (a body
    may hold more @@ lines; they are rewritten with the right counts)."""
    hunks = [[]]
    for line in body[:-1].split("\n") if body.endswith("\n") else body.split("\n"):
        if line.startswith("@@"):
            hunks.append([])
        else:
            hunks[-1].append(line)
    out = f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
    for hunk in hunks:
        old, new = sum(l[:1] in " -" for l in hunk), sum(l[:1] in " +" for l in hunk)
        out += f"@@ -1,{old} +1,{new} @@\n" + "".join(l + "\n" for l in hunk)
    return out


test_file.__test__ = False  # a helper, not a test

FIXTURES = test_file("+def test_card_is_held():\n"
                     f'+    assert scan.card_hit("{CARD}")  # a fake visa\n'
                     f'+    assert scan.has_secret("{STRIPE}")\n'
                     f"+    assert scan.has_secret('{PW}')\n")
DIFF = PRODUCT + FIXTURES


def test_the_reported_case_is_judged_without_its_fixtures():
    """The live failure: a worktree diff with test card numbers was refused whole."""
    assert pb.has_secret(DIFF)
    out, withheld = pb.mask_secrets(DIFF)
    assert out is not None and not pb.has_secret(out) and withheld == ["tests/test_scan.py"]
    assert out.startswith(PRODUCT)  # the product file is sent as it is
    assert out.endswith("+++ b/tests/test_scan.py\n" + pb.SECRET_WITHHELD.format(n=5) + "\n")
    assert "hunter2" not in out and "4000" not in out and "card_is_held" not in out


# Every part of each secret below reached the judge under some masking-inside-a-file rule.
LEAKS = {
    "card-with-cvv": (f"+CARD = '{CARD} 12/29 737'\n", ["12/29 737"]),
    "stripe-concat": (f"+KEY = ('{STRIPE}'\n+       'TAILabcdef99')\n", ["TAILabcdef99"]),
    "jwt-signature": (f"+{TOK} = '{JWT_HEAD}.{JWT_BODY}.{JWT_SIG}'\n", [JWT_SIG]),
    "jwt-wrapped": (f"+curl -H 'Authorization: Bearer {JWT_HEAD}.{JWT_BODY}.\\\n+{JWT_SIG}'\n", [JWT_SIG]),
    "basic-wrapped": ("+H = ('Authorization: Basic " + BASIC[:12] + "'\n+     '" + BASIC[12:] + "')\n", [BASIC[12:]]),
    "aws-csv-row": (f"+deploy,{AWS_ID},{AWS_SK}\n", [AWS_SK]),
    "pem-body": ("+" + PEM + "\n" + "".join("+" + b + "\n" for b in KEY_BODY) + "+" + PEM_END + "\n", KEY_BODY),
    "pem-before-hunk-start": ("".join(" " + b + "\n" for b in KEY_BODY[:2]) + " " + PEM_END + "\n+" + PEM + "\n+"
                              + KEY_BODY[2] + "\n", KEY_BODY),
    "pem-second-hunk": (" " + PEM + "\n-" + KEY_BODY[0] + "\n+" + KEY_BODY[1] + "\n@@ -20,2 +20,2 @@\n-"
                        + KEY_BODY[2] + "\n " + PEM_END + "\n", KEY_BODY),
    "pem-py-paren": ('+KEY = (\n+    "' + PEM + '\\n"\n' + "".join('+    "' + b + '\\n"\n' for b in KEY_BODY) + "+)\n",
                     KEY_BODY),
    "pem-js-plus": ('+const KEY = "' + PEM + '\\n"\n' + "".join('+  + "' + b + '\\n"\n' for b in KEY_BODY), KEY_BODY),
    "yaml-block": ("+db:\n+  " + PWD + ": |  # rotated\n+    " + VALUE + "\n+  host: x\n", [VALUE]),
    "yaml-plain-multiline": ("+" + PWD + ":\n+  hunter2xyz\n+  " + VALUE + "\n", [VALUE]),
    "py-dict-concat": ("+cfg = dict(\n+    " + PWD + '="hunter2xyz"\n+    "' + VALUE + '",\n+)\n', [VALUE]),
    "js-template": ("+const " + PWD + " = `\n+" + VALUE + "\n+`;\n", [VALUE]),
    "shell-heredoc": ("+" + PWD + "=$(cat <<EOF\n+" + VALUE + "\n+EOF\n+)\n", [VALUE]),
    "pem-tab-header": ("+-----BEGIN\tRSA " + "PRIVATE KEY-----\n" + "".join("+" + b + "\n" for b in KEY_BODY), KEY_BODY),
}


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_no_part_of_a_secret_reaches_the_judge(name):
    body, parts = LEAKS[name]
    text = test_file(body, "fixtures/creds.txt") + PRODUCT
    assert pb.has_secret(text)  # origin/main refused this text whole
    out, withheld = pb.mask_secrets(text)
    assert out is not None and not pb.has_secret(out) and withheld == ["fixtures/creds.txt"]
    for part in parts:
        assert part not in out, (name, part)
    assert out.endswith(PRODUCT)


def test_a_file_shown_twice_is_withheld_in_every_section(tmp_path):
    """build_cycle's worktree_diff joins the committed and the uncommitted diff, so a file
    edited in both shows twice; the later section of a key's body has no BEGIN line of its own."""
    sys.path.insert(0, str(SKILL.parent / "super-jev-build-cycle"))
    bc = load("build_cycle_mask", SKILL.parent / "super-jev-build-cycle" / "build_cycle.py")
    body = [f"{k:02d}" + KEY_BODY[k % 3][2:] for k in range(25)]
    origin, wt = tmp_path / "origin", tmp_path / "wt"

    def git(cwd, *args):
        subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "README").write_text("x\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "init")
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True)
    (wt / "fx").mkdir()
    (wt / "fx/key.pem").write_text("\n".join([PEM, *body, PEM_END]) + "\n")
    (wt / "scan.py").write_text("def scan(t):\n    return card_hit(t)\n")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "add")
    edited = body[:15] + [b[::-1] for b in body[15:19]] + body[19:]
    (wt / "fx/key.pem").write_text("\n".join([PEM, *edited, PEM_END]) + "\n")
    text = bc.worktree_diff(type("Ctx", (), {"dir": tmp_path})(), "t", str(wt)).read_text()
    assert text.count("diff --git a/fx/key.pem") == 2 and pb.has_secret(text)
    out, withheld = pb.mask_secrets(text)
    assert out is not None and withheld == ["fx/key.pem"] and "return card_hit(t)" in out
    assert not any(line in out for line in body + edited)


def test_a_renamed_file_is_one_file():
    renamed = ("diff --git a/old.txt b/new.txt\nsimilarity index 90%\nrename from old.txt\nrename to new.txt\n"
               "@@ -1 +1 @@\n-x\n+" + PEM + "\n")
    later = "diff --git a/new.txt b/new.txt\n--- a/new.txt\n+++ b/new.txt\n@@ -5 +5 @@\n-y\n+" + KEY_BODY[0] + "\n"
    out, withheld = pb.mask_secrets(renamed + later + PRODUCT)
    assert KEY_BODY[0] not in out and withheld == ["new.txt"] and out.endswith(PRODUCT)


def test_a_combined_diff_section_is_its_own_file():
    # git diff during a merge conflict writes "diff --cc NAME", not "diff --git"
    committed = test_file("+" + PEM + "\n+" + KEY_BODY[0] + "\n", "k.pem")
    conflict = ("diff --cc k.pem\nindex 1111111,2222222..0000000\n--- a/k.pem\n+++ b/k.pem\n"
                "@@@ -5,1 -5,1 +5,5 @@@\n++<<<<<<< HEAD\n +" + KEY_BODY[1] + "\n++=======\n+ "
                + KEY_BODY[2] + "\n++>>>>>>> other\n")
    out, withheld = pb.mask_secrets(committed + PRODUCT + conflict)  # after a clean file
    assert withheld == ["k.pem"] and PRODUCT in out
    assert not any(b in out for b in KEY_BODY)


def test_a_held_section_without_a_hunk_keeps_only_header_lines():
    first = test_file("+" + PEM + "\n", "k.pem")
    no_hunk = "diff --git a/k.pem b/k.pem\nindex 1..2 100644\n" + KEY_BODY[0] + "\n"
    out, withheld = pb.mask_secrets(first + no_hunk + PRODUCT)
    assert out is None and "k.pem" in withheld  # not git's output: the whole item is one file


def test_gate_masks_before_it_truncates(tmp_path):
    """A cut keeps the tail of the oldest evidence. Cutting the raw diff here drops the key's
    BEGIN line and keeps its body, which then scans clean; the gate masks first, then cuts."""
    body = [f"{k:02d}{KEY_BODY[k % 3][2:]}" for k in range(60)]
    key = test_file("+" + PEM + "\n" + "".join(f"+{b}\n" for b in body), "fx/key.pem")
    clean = test_file("".join(f"+x{k} = {k}  # a long enough clean line of code\n" for k in range(650)), "src/big.py")
    ev = tmp_path / "wt.diff"
    ev.write_text(key + clean + PRODUCT)
    cap_chars = 8000 * 4
    assert 0 < len(key + clean + PRODUCT) - cap_chars < len(key) - 200  # the raw cut lands in the body
    argv = [str(SKILL / "superjev.py"), "gate", str(ev), "--claim", "scan returns card_hit(text)"]
    p, sent = run_cli(tmp_path, argv, SUPERJEV_INPUT_CAP_TOK="8000")
    assert sent, p.stdout + p.stderr
    state = json.loads(sent.strip().split("\n")[0])["state"]
    assert "return card_hit(text)" in state and not any(b in state for b in body)


def test_one_file_in_two_evidence_items_is_withheld_in_both():
    """A committed patch and an uncommitted diff given as two evidence files: the second holds a
    hunk from the middle of the key, with no marker, and alone scans clean."""
    committed = test_file("+" + PEM + "\n" + "".join(f"+{b}\n" for b in KEY_BODY), "fx/key.pem") + PRODUCT
    uncommitted = test_file(f" {KEY_BODY[1]}\n-{KEY_BODY[2]}\n+{KEY_BODY[2][::-1]}\n", "fx/key.pem")
    assert not pb.has_secret(uncommitted)
    items, withheld = pb.mask_evidence([("c.diff", committed), ("u.diff", uncommitted)])
    assert items[1] == ("u.diff", None) and "fx/key.pem" in withheld
    assert items[0][1].endswith(PRODUCT) and not any(b in items[0][1] for b in KEY_BODY)


def test_text_around_a_diff_is_one_file_with_it():
    """The Stop hook's window mixes tool output: a read showing a key's BEGIN line, a diff, then a
    read of the key's body alone, which by itself scans clean."""
    window = ("Read fx/key.pem 1-2:\n" + PEM + "\n" + PRODUCT + "Read fx/key.pem 3-5:\n"
              + "\n".join(KEY_BODY) + "\n")
    assert pb.mask_secrets(window)[0] is None


def test_a_read_of_a_diffed_key_is_one_file_with_its_diff():
    """The Stop hook's window: a diff of a key (its BEGIN line as context), another file, then a
    Read of the key's tail, which alone scans clean."""
    key = test_file(" " + PEM + "\n-" + KEY_BODY[0] + "\n+" + KEY_BODY[0][::-1] + "\n", "fx/key.pem")
    window = key + PRODUCT + "Read fx/key.pem offset=20:\n" + "\n".join(KEY_BODY[1:]) + "\n"
    assert pb.mask_secrets(window)[0] is None


def test_gate_with_no_evidence_left_after_the_cap_is_not_a_secret_refusal(tmp_path):
    ev, draft = tmp_path / "clean.diff", tmp_path / "draft.md"
    ev.write_text(PRODUCT)
    draft.write_text("scan returns card_hit(text). " * 800)
    p, _sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(ev), "--draft", str(draft),
                                  "--claim", "scan returns card_hit(text)"], SUPERJEV_INPUT_CAP_TOK="1000")
    assert "contains a secret" not in p.stdout + p.stderr


def test_cli_entries_withhold_a_file_split_over_two_evidence_files(tmp_path):
    committed, uncommitted = tmp_path / "c.diff", tmp_path / "u.diff"
    committed.write_text(test_file("+" + PEM + "\n" + "".join(f"+{b}\n" for b in KEY_BODY), "fx/key.pem") + PRODUCT)
    uncommitted.write_text(test_file(f" {KEY_BODY[1]}\n-{KEY_BODY[2]}\n+{KEY_BODY[2][::-1]}\n", "fx/key.pem"))
    claim = ["--claim", "scan returns card_hit(text)"]
    for argv in ([str(SKILL / "superjev.py"), "gate", str(committed), str(uncommitted), *claim],
                 [str(SKILL / "lib" / "jev_client.py"), str(committed), str(uncommitted), *claim],
                 [str(SKILL / "dispatch.py"), "check", str(committed), str(uncommitted), *claim]):
        p, sent = run_cli(tmp_path, argv)
        assert sent, argv
        assert not any(b in sent or b[::-1] in sent for b in KEY_BODY), argv
        (tmp_path / "sent.log").unlink()


def test_a_lone_cr_cannot_start_a_fake_file(tmp_path):
    """Text mode reads a lone CR as a line break, so "+log 100%\\rdiff --git a/zz b/zz" became a
    file of its own, judged apart from the key it sat in. Readers now keep the CR in its line,
    and a hunk that ends before its counts joins the rest of its evidence item."""
    fake = f"+fetching 100%\rdiff --git a/zz b/zz\n"
    body = "".join(f"+{b}\n" for b in KEY_BODY)
    text = test_file("+" + PEM + "\n" + fake + body + "+" + PEM_END + "\n", "tests/fixture.log") + PRODUCT
    for as_read in (text, text.replace("\r", "\n")):  # kept by the readers, and split by text mode
        out, _ = pb.mask_secrets(as_read)
        assert out is None or not any(b in out for b in KEY_BODY)
    out, _ = pb.mask_secrets(text)
    assert out.endswith(PRODUCT)  # with the CR kept, nothing else is lost
    ev = tmp_path / "wt.diff"
    ev.write_bytes(text.encode())
    for argv in ([str(SKILL / "superjev.py"), "gate", str(ev), "--claim", "scan returns card_hit(text)"],
                 [str(SKILL / "lib" / "jev_client.py"), str(ev), "--claim", "scan returns card_hit(text)"]):
        p, sent = run_cli(tmp_path, argv)
        assert sent and not any(b in sent for b in KEY_BODY), argv
        (tmp_path / "sent.log").unlink()


def test_worktree_diff_keeps_a_lone_cr_in_its_line(tmp_path):
    bc = load("build_cycle_cr", SKILL.parent / "super-jev-build-cycle" / "build_cycle.py")
    origin, wt = tmp_path / "origin", tmp_path / "wt"

    def git(cwd, *args):
        subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "README").write_text("x\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "init")
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True)
    (wt / "log.txt").write_bytes(b"a 100%\rdiff --git a/zz b/zz\nb\n")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "log")
    text = bc.worktree_diff(type("Ctx", (), {"dir": tmp_path})(), "t", str(wt)).read_bytes()
    assert b"+a 100%\rdiff --git a/zz b/zz\n" in text


def test_gate_evidence_mode_names_what_it_withheld(tmp_path):
    notes, other = tmp_path / "notes.md", tmp_path / "other.md"
    notes.write_text(f"card {CARD}\n")
    other.write_text("scan returns card_hit(text)\n")
    p, sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(notes), str(other),
                                 "--claim", "scan returns card_hit(text)"])
    assert "4000" not in sent and "card_hit" in sent
    assert "withheld 1 file(s)" in p.stdout and str(notes) in p.stdout


def test_non_ascii_digit_in_another_item_still_masks():
    # the Luhn switch is evidence-wide: a digit run that is only a card with Luhn off must not
    # slip through the "nothing to mask" check and get the whole request refused later
    items, withheld = pb.mask_evidence([("notes.md", "label \u0663\n"),
                                        ("wt.diff", test_file("+n = '1234 5678 9012 3456'\n", "t.py") + PRODUCT)])
    assert withheld == ["t.py"] and items[1][1].endswith(PRODUCT)


def test_paths_with_spaces_join_only_their_own_file():
    held = test_file(f"+x = '{CARD}'\n", "dir with space/a.py")
    clean = test_file("+y = 1\n", "dir with space/b.py")
    out, withheld = pb.mask_secrets(held + clean)
    assert withheld == ["dir with space/a.py"] and out.endswith(clean)


def test_a_long_evidence_name_does_not_crash_the_gate(tmp_path):
    ev = tmp_path / ("x" * 240 + ".diff")
    ev.write_text(FIXTURES + PRODUCT)
    p, sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(ev), "--claim", "scan returns card_hit(text)"])
    assert sent and "File name too long" not in p.stderr


def test_text_that_is_not_a_diff_is_not_judged():
    assert pb.mask_secrets(f"notes\nthe card is {CARD}\n") == (None, ["the text"])


def test_a_secret_shaped_header_is_withheld_too():
    name = "password: " + "hunter2xyz9"
    text = f"diff --git a/x b/x {name}\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+x = 1\n"
    assert pb.has_secret(text)
    out, withheld = pb.mask_secrets(text + PRODUCT)
    assert out == pb.SECRET_WITHHELD.format(n=5) + "\n" + PRODUCT and withheld == ["a file"]
    assert pb.mask_secrets(text) == (None, ["a file", "the text"])  # nothing judgeable left


def test_text_outside_a_diff_makes_its_item_one_file():
    out, withheld = pb.mask_secrets(f"summary: rotate {PW}\n" + PRODUCT)
    assert out is None and "the text" in withheld
    assert pb.mask_secrets("summary: all clean\n" + PRODUCT) == ("summary: all clean\n" + PRODUCT, [])


def test_non_ascii_digit_in_one_file_keeps_the_others_judged():
    # a non-ASCII digit turns the whole-text card check's Luhn test off; parts must match it
    other = test_file("+label = '\u0661\u0662'\n+TRACK = '9302 2110 4790 0005 3721 11'\n", "labels.py")
    out, withheld = pb.mask_secrets(FIXTURES + other + PRODUCT)
    assert out is not None and not pb.has_secret(out) and out.endswith(PRODUCT)


def test_clean_text_is_returned_unchanged():
    text = "commit 5f2a9c1e0b\n2026-09-28 run 1234 5678 took 12s\nhttps://x.test/?id=4000056655665556\n"
    assert pb.mask_secrets(text) == (text, [])


def test_many_files_mask_quickly():
    text = "".join(test_file(f"+v = '{CARD}'\n", f"t{k}.py") + PRODUCT for k in range(2000))
    t = time.process_time()
    out, withheld = pb.mask_secrets(text)
    assert len(withheld) == 2000 and time.process_time() - t < 20


class Judge:
    def __init__(self):
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        return {"answers": {k: {"noul": 0.9} for k in questions}}


def test_code_gate_judges_the_diff_without_the_withheld_file():
    judge = Judge()
    info = {}
    rows, code = sj.run_code_gate([("wt.diff", DIFF)], ["scan returns card_hit(text)"],
                                  ask_fn=judge, mask_info=info)
    (state, _q), = judge.calls
    assert not pb.has_secret(state) and "return card_hit(text)" in state
    assert "4000" not in state and "hunter2" not in state and code == 0
    assert info["withheld"] == ["tests/test_scan.py"]


def test_code_gate_drops_a_plain_evidence_file_and_keeps_the_rest():
    judge = Judge()
    sj.run_code_gate([("notes.md", f"card {CARD}\n"), ("wt.diff", PRODUCT)],
                     ["scan returns card_hit(text)"], ask_fn=judge)
    (state, _q), = judge.calls
    assert "notes.md" not in state and "return card_hit(text)" in state


def test_code_gate_refuses_when_no_evidence_is_left():
    with pytest.raises(ValueError, match="contains a secret; not sent"):
        sj.run_code_gate([("notes.md", f"card {CARD}\n")], ["x is y"], ask_fn=Judge())


def test_code_gate_pattern_arm_still_reads_the_local_text():
    ev = 'import re\nCARD_RE = re.compile(r"[0-9]{4}( [0-9]{4}){3}")\n'
    judge = Judge()
    rows, code = sj.run_code_gate([("ev", ev + f"# {CARD}\n")], [f"`{CARD}` matches CARD_RE"],
                                  ask_fn=judge)
    assert judge.calls == [] and rows[0]["verdict"] == "SUPPORTED"


def test_pattern_only_claims_never_need_the_mask(monkeypatch):
    # nothing goes to the judge, so evidence with nothing judgeable does not refuse them
    monkeypatch.setattr(sj, "_mask_evidence", lambda items: pytest.fail("masked for nothing"))
    ev = 'import re\nFOO_RE = re.compile(r"foo")\n'
    rows, code = sj.run_code_gate([("ev", ev)], ["`foo` matches FOO_RE"], ask_fn=Judge())
    assert rows[0]["verdict"] == "SUPPORTED" and code == 0


def test_ask_still_refuses_raw_secret_text(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-key")
    monkeypatch.setattr(jc, "transport", lambda *a: pytest.fail("secret was sent"))
    with pytest.raises(jc.SecretBlocked, match="contains a secret; not sent"):
        jc.ask("CODE:\n" + DIFF, {"c1": {"instructions": "Is it?", "criteria": {"YES": "y"}}})


def test_check_drops_a_secret_file_and_names_it(tmp_path, monkeypatch, capsys):
    notes, diff = tmp_path / "notes.md", tmp_path / "wt.diff"
    notes.write_text(f"card {CARD}\n")
    diff.write_text(DIFF)
    sent = []
    monkeypatch.setattr(jc, "check", lambda ev, *a, **k: sent.append(ev) or ([], {}, 0))
    monkeypatch.setattr(jc, "print_table", lambda *a, **k: None)
    assert jc.main([str(notes), str(diff), "--claim", "scan returns card_hit(text)"]) == 0
    (path, text), = sent[0]
    assert path == str(diff) and text.startswith(PRODUCT) and not pb.has_secret(text)
    err = capsys.readouterr().err
    assert "withheld 2 file(s)" in err and str(notes) in err and "tests/test_scan.py" in err


SITE = """import urllib.request, os
def _rec(req, *a, **k):
    open(os.environ["MASK_SENT"], "ab").write(req.data + b"\\n")
    raise OSError("blocked in test")
def _rec_open(self, req, *a, **k):  # the client posts through its own no-redirect opener
    return _rec(req)
urllib.request.urlopen = _rec
urllib.request.OpenerDirector.open = _rec_open
"""


def run_cli(tmp_path, argv, **extra_env):
    (tmp_path / "site").mkdir(exist_ok=True)
    (tmp_path / "site" / "sitecustomize.py").write_text(SITE)
    sent = tmp_path / "sent.log"
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "site"), MASK_SENT=str(sent))
    env["TYPESAFE_API_" + "KEY"] = "test-not-a-key"
    env.pop("SUPERJEV_GATE_CMD", None)
    env.update(extra_env)
    p = subprocess.run([sys.executable, *argv], env=env, capture_output=True, text=True, timeout=120)
    return p, (sent.read_text() if sent.exists() else "")


@pytest.mark.parametrize("entry", ["gate-code", "check"])
def test_cli_sends_the_diff_without_its_secret_files(tmp_path, entry):
    """End to end, over the recorded wire: the request goes out, holding the product file
    and no part of any leak case."""
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF + "".join(test_file(body, f"fx/{n}.txt") for n, (body, _p) in sorted(LEAKS.items())))
    claim = ["--claim", "scan returns card_hit(text)"]
    argv = {"gate-code": [str(SKILL / "superjev.py"), "gate", str(ev), *claim],
            "check": [str(SKILL / "lib" / "jev_client.py"), str(ev), *claim]}[entry]
    p, sent = run_cli(tmp_path, argv)
    assert sent, p.stdout + p.stderr
    for body in sent.strip().split("\n"):
        state = json.loads(body)["state"]
        assert "return card_hit(text)" in state and not pb.has_secret(state)
        assert "4000" not in state and "hunter2" not in state
        for _body, parts in LEAKS.values():
            assert not any(part in state for part in parts)
    assert "contains a secret; not sent" not in p.stdout + p.stderr
    if entry == "check":  # the gate prints its note with the verdict, which the blocked wire stops
        assert f"withheld {len(LEAKS) + 1} file(s)" in p.stderr


def test_cli_claim_holding_a_secret_is_still_refused(tmp_path):
    ev = tmp_path / "wt.diff"
    ev.write_text(DIFF)
    p, sent = run_cli(tmp_path, [str(SKILL / "superjev.py"), "gate", str(ev),
                                 "--claim", f"the test uses {CARD}"])
    assert sent == "" and p.returncode != 0 and "CLEAN" not in p.stdout


@pytest.mark.parametrize("entry", ["gate", "check"])
def test_all_secret_evidence_bigger_than_one_call_is_never_split(tmp_path, entry):
    """The fourth review: a big log whose only secret is near its end was split raw,
    and its clean first part went to the judge. It goes whole and is refused."""
    log = tmp_path / "big.log"
    log.write_text("".join(f"row {i}: an ordinary line of a long run log\n" for i in range(3000))
                   + PEM + "\n" + "\n".join(KEY_BODY) + "\n" + PEM_END + "\n")
    argv = {"gate": [str(SKILL / "superjev.py"), "gate"],
            "check": [str(SKILL / "dispatch.py"), "check"]}[entry]
    p, sent = run_cli(tmp_path, [*argv, str(log), "--claim", "the run log has three thousand rows"])
    assert not sent, sent[:200]
    assert "contains a secret" in p.stdout + p.stderr
