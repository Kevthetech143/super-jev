#!/usr/bin/env python3
"""Offline tests for audit_visibility.py: read-only pointer-visibility audit.

Builds a fixture registry.json (path-connect style) and a fixture
verified-pointer-memory sqlite database directly, with no live Super Jev
state and no network.

    python3 -m pytest skills/super-jev/tests/test_audit_visibility.py -q
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "audit_visibility.py"

spec = importlib.util.spec_from_file_location("audit_visibility", SCRIPT)
av = importlib.util.module_from_spec(spec)
spec.loader.exec_module(av)


def make_registry(tmp_path, datasets):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"version": 1, "datasets": datasets}))
    return path


def make_pointers_db(tmp_path, pointers):
    """pointers: {name: {"dataset": str, "principals": [str, ...]}}"""
    path = tmp_path / "memory.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE pointers(name TEXT PRIMARY KEY, body TEXT NOT NULL)")
    for name, body in pointers.items():
        conn.execute("INSERT INTO pointers VALUES (?, ?)", (name, json.dumps(body)))
    conn.commit()
    conn.close()
    return path


def dataset_entry(connect_principals, originals=None):
    return {
        "pathConnection": {"pointer": "x", "principals": sorted(connect_principals)},
        "originals": [{"path": p, "sha256": "0" * 64} for p in (originals or [])],
    }


def test_no_datasets_no_pointers_is_clean(tmp_path):
    registry = make_registry(tmp_path, {})
    db = make_pointers_db(tmp_path, {})
    result = av.audit(registry, db)
    assert result == {"principals": {}}


def test_visible_and_connected_by_same_principal_is_clean(tmp_path):
    registry = make_registry(tmp_path, {
        "health-reviewed": dataset_entry(["health-fitness"], ["/Users/admin/agents/health-fitness-brain/notes.md"]),
    })
    db = make_pointers_db(tmp_path, {
        "health-reviewed": {"dataset": "health-reviewed", "principals": ["health-fitness"]},
    })
    result = av.audit(registry, db)
    hf = result["principals"]["health-fitness"]
    assert hf["visible"] == ["health-reviewed"]
    assert hf["connected"] == ["health-reviewed"]
    assert hf["flags"] == []


def test_visible_but_not_connected_is_flagged(tmp_path):
    # "content-check" was connected only by primary, but is also visible to
    # health-fitness -- the exact shape of the reported pain case.
    registry = make_registry(tmp_path, {
        "content-check": dataset_entry(["primary"], ["/Users/admin/agents/primary-brain/notes.md"]),
    })
    db = make_pointers_db(tmp_path, {
        "content-check": {"dataset": "content-check", "principals": ["primary", "health-fitness"]},
    })
    result = av.audit(registry, db)
    hf_flags = result["principals"]["health-fitness"]["flags"]
    flag_types = {f["type"] for f in hf_flags}
    assert "visible-but-not-connected" in flag_types
    assert all(f["pointer"] == "content-check" for f in hf_flags)
    # The principal that did connect it is not flagged for that pointer.
    primary_flags = [f for f in result["principals"]["primary"]["flags"]
                     if f["pointer"] == "content-check"]
    assert primary_flags == []


def test_foreign_brain_root_is_flagged(tmp_path):
    registry = make_registry(tmp_path, {
        "shared": dataset_entry(
            ["health-fitness"], ["/Users/admin/agents/primary-brain/private-notes.md"]
        ),
    })
    db = make_pointers_db(tmp_path, {
        "shared": {"dataset": "shared", "principals": ["health-fitness"]},
    })
    result = av.audit(registry, db)
    hf_flags = result["principals"]["health-fitness"]["flags"]
    types = {f["type"] for f in hf_flags}
    assert "foreign-brain-root" in types


def test_own_brain_root_is_not_flagged(tmp_path):
    registry = make_registry(tmp_path, {
        "own": dataset_entry(
            ["health-fitness"], ["/Users/admin/agents/health-fitness-brain/notes.md"]
        ),
    })
    db = make_pointers_db(tmp_path, {
        "own": {"dataset": "own", "principals": ["health-fitness"]},
    })
    result = av.audit(registry, db)
    assert result["principals"]["health-fitness"]["flags"] == []


def test_missing_registry_and_db_are_treated_as_empty(tmp_path):
    result = av.audit(tmp_path / "no-registry.json", tmp_path / "no.sqlite3")
    assert result == {"principals": {}}


def test_run_requires_registry_and_db_args():
    code, result = av.run([])
    assert code == 2
    assert result["status"] == "error"


def test_run_filters_by_principal(tmp_path):
    registry = make_registry(tmp_path, {
        "content-check": dataset_entry(["primary"]),
    })
    db = make_pointers_db(tmp_path, {
        "content-check": {"dataset": "content-check", "principals": ["primary", "health-fitness"]},
    })
    code, result = av.run([
        "--registry", str(registry), "--db", str(db), "--principal", "health-fitness",
    ])
    assert code == 0
    assert result["status"] == "flagged"
    assert list(result["principals"].keys()) == ["health-fitness"]


def test_run_clean_status_when_no_flags(tmp_path):
    registry = make_registry(tmp_path, {
        "health-reviewed": dataset_entry(["health-fitness"]),
    })
    db = make_pointers_db(tmp_path, {
        "health-reviewed": {"dataset": "health-reviewed", "principals": ["health-fitness"]},
    })
    code, result = av.run(["--registry", str(registry), "--db", str(db)])
    assert code == 0
    assert result["status"] == "clean"
