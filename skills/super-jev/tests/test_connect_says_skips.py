#!/usr/bin/env python3
"""Connect says every file it leaves out by a default rule, and the way in.

A folder named documents/ under --root once vanished from connect with no word, and
code files were dropped silently. One rule: every file under a --root that a default
rule leaves out is counted in connect's output by reason (counts and folder names or
extensions, never file names), with the way to include it when there is one. Skipping
never changes which files connect or the exit code.

    python3 -m pytest skills/super-jev/tests/test_connect_says_skips.py -q
"""
import importlib.util
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("prepare_bulk_skips", SKILL / "prepare_bulk.py")
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

TREE = {
    "policies/expense.md": "# Expense policy\n\nHotels up to 220 dollars per night.\n",
    "documents/acme-contract.md": "# Acme contract\n\nRenewal is due 2027-03-01.\n",
    "documents/old/acme-2025.md": "# Acme 2025\n\nOld terms.\n",
    "profile/team.md": "# Team\n\nThe support lead is Wren Halloway.\n",
    "node_modules/pkg/README.md": "# pkg\n\nPackage notes.\n",
    ".drafts/draft.md": "# Draft\n\nA hidden draft.\n",
    "policies/expense.bak.md": "# Old expense policy\n\nold copy\n",
    "policies/empty.md": "",
    "policies/matcher.py": "TOLERANCE = 0.02\n",
    "policies/rates.csv": "city,rate\n",
    "node_modules/pkg/index.js": "module.exports = 1\n",
}


def _tree(root: Path) -> None:
    for rel, text in TREE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _skip_lines(out: str) -> list:
    return [ln for ln in out.splitlines() if ln.strip().startswith("SKIP")]


def test_markdown_in_default_skipped_folders_is_counted_with_the_way_in(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    files, held = pb.inventory([root])
    assert [p.name for p in files] == ["expense.md"] and held == []  # what connects is unchanged
    line = next((ln for ln in _skip_lines(capsys.readouterr().out) if "documents/" in ln), "")
    assert line, "no SKIP line names the documents/ folder"
    for folder, n in (("documents/", 2), ("profile/", 1), ("node_modules/", 1), (".drafts/", 1)):
        assert f"{folder} ({n})" in line, (folder, line)
    assert re.search(r"\b5\b", line), line          # 5 Markdown files left out by folder
    assert "--root" in line, line                   # the way in: connect the folder by itself ...
    assert "--pointer" in line, line                # ... as its own set: reusing a pointer replaces that set's files


def test_other_file_types_are_counted_outside_skipped_folders(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    pb.inventory([root])
    line = next((ln for ln in _skip_lines(capsys.readouterr().out) if ".py" in ln), "")
    assert line, "no SKIP line counts the .py file"
    assert ".csv" in line and ".js" not in line, line   # index.js sits in node_modules/: not counted twice
    assert re.search(r"\b2\b", line) and ".md" in line, line


def test_backup_named_and_empty_markdown_are_counted(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    pb.inventory([root])
    lines = _skip_lines(capsys.readouterr().out)
    assert any("backup" in ln and re.search(r"\b1\b", ln) for ln in lines), lines
    assert any("empty" in ln and re.search(r"\b1\b", ln) for ln in lines), lines


def test_hidden_markdown_files_are_counted_with_the_way_in(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    (root / "a" / ".scratch.md").write_text("# Scratch\n\nhidden text\n")
    files, _ = pb.inventory([root])
    assert [p.name for p in files] == ["note.md"]
    lines = _skip_lines(capsys.readouterr().out)
    assert any("hidden" in ln and "rename" in ln and re.search(r"\b1\b", ln) for ln in lines), lines


def test_a_link_outside_every_root_is_counted_with_the_way_in_and_never_named(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "vendor-pricing.md").write_text("# Vendor pricing\n\nPer seat.\n")
    (root / "a" / "pricing-link.md").symlink_to(outside / "vendor-pricing.md")
    files, _ = pb.inventory([root])
    assert [p.name for p in files] == ["note.md"]
    out = capsys.readouterr().out
    line = next((ln for ln in _skip_lines(out) if "--allow-target" in ln), "")
    assert line and re.search(r"\b1\b", line), out
    assert "vendor-pricing" not in out and "pricing-link" not in out, out   # a file name is never printed
    assert str(tmp_path) not in out, out                                    # nor any path
    files, _ = pb.inventory([root], allow_targets=[outside])               # the printed way in works
    assert sorted(p.name for p in files) == ["note.md", "pricing-link.md"]


def test_a_prepared_dataset_copy_is_counted_and_says_where_it_connects(tmp_path, capsys):
    dataset = tmp_path / ".local" / "retrieval-datasets" / "passages"
    dataset.mkdir(parents=True)
    (dataset / "p1.md").write_text("# Passage\n\ncopied text\n")
    assert pb.inventory([dataset]) == ([], [])
    lines = _skip_lines(capsys.readouterr().out)
    assert any("dataset" in ln and re.search(r"\b1\b", ln) for ln in lines), lines


def test_long_lists_are_capped_and_still_total_correctly(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    for i, ext in enumerate(["pdf", "docx", "csv", "json", "png", "jpg", "zip", "txt", "xlsx"]):
        (root / f".d{i}").mkdir()
        (root / f".d{i}" / "x.md").write_text("# X\n\ntext\n")
        (root / "a" / f"data.{ext}").write_text("x")
    pb.inventory([root])
    lines = _skip_lines(capsys.readouterr().out)
    folders = next((ln for ln in lines if ".d0/" in ln), "")
    types = next((ln for ln in lines if "other types" in ln), "")
    assert re.search(r"\b9\b", folders) and re.search(r"\+\d+ more", folders), folders
    assert re.search(r"\b9\b", types) and re.search(r"\+\d+ more", types), types
    assert len(folders) < 400 and len(types) < 400


def test_skip_lines_never_name_a_skipped_file(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    pb.inventory([root])
    out = "\n".join(_skip_lines(capsys.readouterr().out))
    for name in ("acme-contract", "acme-2025", "team.md", "draft.md", "matcher.py"):
        assert name not in out, name


def test_a_dotted_name_that_is_not_an_extension_is_never_printed(tmp_path, capsys):
    """What follows a name's last dot can be part of the name (a token, a person, a client, an account).
    Only an extension on the known list is named; everything else is counted as "other"."""
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    names = ["file0.sk_live_51HxAbCdEfGh1234567890", "file1. wren halloway", "file2.2 for acme corp",
             "file3.AKIAIOSFODNN7EXAMPLE", "Dr.Halloway", "client.acme", "acct.40128888", "file7.draft"]
    for name in names:
        (root / "a" / name).write_text("x")
    (root / "a" / "matcher.py").write_text("x")
    (root / "a" / "Makefile").write_text("x")
    pb.inventory([root])
    out = "\n".join(_skip_lines(capsys.readouterr().out)).lower()
    for frag in ("sk_live", "wren", "halloway", "acme", "akia", "40128888", "draft", "makefile"):
        assert frag not in out, (frag, out)
    assert ".py 1" in out and "other 9" in out, out


def test_walk_md_reports_a_skipped_folder_once_not_once_per_file(tmp_path):
    """Files of other types in a skipped folder are one entry per folder with a count, and nothing in a
    hidden folder is reported: a node_modules/ or .git/ with tens of thousands of files must not cost one
    path operation per file (connect runs this on every refresh)."""
    root = tmp_path / "notes"
    for rel in ("src/app.py", "node_modules/pkg/index.js", "node_modules/pkg/util.js", "node_modules/pkg/b.json",
                ".git/objects/ab12", ".cache/x/y.py", "documents/rates.csv", "documents/lease.pdf",
                "documents/.DS_Store", "src/.hidden.py", ".DS_Store"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    others = []
    pb.walk_md(root, others=others)
    entries = {(rel, reason, label, n) for _key, rel, reason, label, n in others}
    assert entries == {("src/app.py", "types", ".py", 1),
                       ("node_modules/pkg", "folder_other", "node_modules/", 3),
                       ("documents", "folder_other", "documents/", 2)}, entries
    others = []
    pb.walk_md(root, no_recurse=True, others=others)
    assert others == [], others   # no top-level file here is of another type; hidden ones never count


def test_a_skipped_folder_holding_only_other_types_is_still_named(tmp_path, capsys):
    """A documents/ folder of PDFs and Word files used to vanish with no word at all."""
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    for rel in ("documents/lease.pdf", "documents/contract.docx", "documents/.DS_Store",
                "node_modules/pkg/index.js", ".git/objects/ab12", ".venv/lib/x.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    files, _ = pb.inventory([root])
    assert [p.name for p in files] == ["note.md"]
    out = capsys.readouterr().out
    line = next((ln for ln in _skip_lines(out) if "documents/" in ln), "")
    assert line and "documents/ (2)" in line and "node_modules/ (1)" in line, out
    assert "other types" in line and re.search(r"\b3\b", line), line
    assert ".git" not in out and ".venv" not in out and "DS_Store" not in out, out   # hidden is never counted
    for name in ("lease", "contract", "index.js"):
        assert name not in out, name   # a file name is never printed


def test_one_physical_file_reached_two_ways_is_counted_once(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "documents").mkdir(parents=True)
    (root / "documents" / "acme.md").write_text("# Acme\n\nterms\n")
    (root / "documents" / "rates.csv").write_text("x")
    (root / "top.py").write_text("x")
    (root / "link.md").symlink_to(root / "documents" / "acme.md")   # a link in the root to a documents/ file
    (root / "docs-alias").symlink_to(root / "documents")            # a folder link to the same folder
    pb.inventory([root])
    out = capsys.readouterr().out
    assert "documents/ (1)" in out and "documents/ (2)" not in out, out
    alias = tmp_path / "alias"
    alias.symlink_to(root)                                           # the same tree reached as a second root
    pb.inventory([root, alias])
    out = capsys.readouterr().out
    assert "documents/ (1)" in out and "documents/ (2)" not in out, out
    assert ".py 1" in out and ".py 2" not in out, out
    assert re.search(r"\b1 file\(s\) of other types in folders skipped", out), out


def test_the_printed_way_in_works(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    files, _ = pb.inventory([root / "documents"])
    assert sorted(p.name for p in files) == ["acme-2025.md", "acme-contract.md"]


def test_a_clean_folder_prints_no_skip_line(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "a").mkdir(parents=True)
    (root / "a" / "note.md").write_text("# Note\n\ntext\n")
    (root / "a" / ".DS_Store").write_text("x")      # system clutter is not user content
    pb.inventory([root])
    assert _skip_lines(capsys.readouterr().out) == []


def test_files_a_user_flag_leaves_out_are_not_counted_as_default_skips(tmp_path, capsys):
    """--exclude and --name are the user's own choice; only default rules are reported."""
    root = tmp_path / "notes"
    _tree(root)
    pb.inventory([root], excludes=["documents", "policies"])
    out = "\n".join(_skip_lines(capsys.readouterr().out))
    assert "documents/" not in out and ".py" not in out and ".csv" not in out, out
    assert "empty" not in out and "backup" not in out, out
    pb.inventory([root], names=["expense.md"])
    out = "\n".join(_skip_lines(capsys.readouterr().out))
    assert ".py" not in out and "empty" not in out and "backup" not in out and "documents/" not in out, out


def test_no_recurse_does_not_count_the_folders_it_never_walks(tmp_path, capsys):
    root = tmp_path / "notes"
    _tree(root)
    (root / "top.md").write_text("# Top\n\ntext\n")
    files, _ = pb.inventory([root], no_recurse=True)
    assert [p.name for p in files] == ["top.md"]
    assert _skip_lines(capsys.readouterr().out) == []


def test_a_file_reached_by_two_roots_is_counted_once(tmp_path, capsys):
    root = tmp_path / "notes"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "expense.md").write_text("# Expense\n\ntext\n")
    (root / "policies" / ".scratch.md").write_text("# Scratch\n\nhidden text\n")
    files, _ = pb.inventory([root, root / "policies"])
    assert [p.name for p in files] == ["expense.md"]
    lines = _skip_lines(capsys.readouterr().out)
    assert any("hidden" in ln and re.search(r"\b1\b", ln) for ln in lines), lines


def _run_main(monkeypatch, tmp_path, root):
    monkeypatch.setattr(pb, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(sys, "argv", ["prepare_bulk.py", "--root", str(root), "--pointer", "notes",
                                      "--principal", "me", "--writer", "builtin", "--no-connect"])
    return pb.main()


def test_skip_lines_never_change_the_exit_code(tmp_path, monkeypatch, capsys):
    root = tmp_path / "notes"
    _tree(root)
    assert _run_main(monkeypatch, tmp_path, root) == 0
    out = capsys.readouterr().out
    assert _skip_lines(out), out
    assert "CONNECTED 0, HELD 0, FAILED 0" in out, out    # --no-connect: nothing sent, nothing held


def test_nothing_left_to_connect_points_at_the_skip_lines_it_follows(tmp_path, monkeypatch, capsys):
    """Every .md file here sits in a skipped folder. The error must not say none were found."""
    root = tmp_path / "notes"
    for rel in ("documents/a.md", "profile/b.md"):
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_text("# Note\n\ntext\n")
    assert _run_main(monkeypatch, tmp_path, root) == 1
    out = capsys.readouterr().out
    assert _skip_lines(out), out
    assert "no .md file left to connect" in out and "SKIP lines" in out, out
    assert "no .md files found" not in out, out
