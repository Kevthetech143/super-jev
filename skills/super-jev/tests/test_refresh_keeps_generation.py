"""A replace:true refresh of a sharded set whose files did not change keeps every shard's generation, so the
ask never falls back for it and the index never purges it; a label-only change still reaches the index.
Made-up files, the real local engine, no Jev, no network.

    python3 -m pytest skills/super-jev/tests/test_refresh_keeps_generation.py -q
"""
import hashlib
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
EXP = SKILL.parents[1] / "experiments" / "verified-pointer-memory"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(EXP))
import prepare_bulk as pb  # noqa: E402
from file_index import FileIndex  # noqa: E402


def engine(tmp_path, monkeypatch):
    import cli
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"db": str(tmp_path / "answers.sqlite"), "registry": str(tmp_path / "registry.json")}))
    config = cli.load_config(cfg)
    monkeypatch.setattr(pb, "memory", lambda req: cli.run(req, config))
    return config


def generations(config):
    import sqlite3
    with sqlite3.connect(config["db"]) as c:
        return {n: json.loads(b)["generation"] for n, b in c.execute("SELECT name, body FROM pointers")}


def test_unchanged_sharded_refresh_keeps_every_shard_generation(tmp_path, monkeypatch):
    config = engine(tmp_path, monkeypatch)
    files = []
    for i in range(4):
        f = tmp_path / "notes" / f"note-{i}.md"
        f.parent.mkdir(exist_ok=True)
        f.write_text(f"# Note {i}\nMade-up fact number {i}.\n")
        files.append(f)
    cache = {str(f): {"description": f"note {i}", "kind": "note", "status": "done", "as_of": "unknown",
                      "subject": "facts"} for i, f in enumerate(files)}
    shards = {"set-1": files[:2], "set-2": files[2:]}
    for name, part in shards.items():
        assert pb.connect_part(name, ["owner"], part, cache)["connected"]
    first = generations(config)
    assert set(first) == set(shards)
    for name, part in shards.items():  # the refresh: every shard reconnects with replace:true
        assert pb.connect_part(name, ["owner"], part, cache)["connected"]
    assert generations(config) == first
    files[0].write_text("# Note 0\nA changed fact.\n")
    assert pb.connect_part("set-1", ["owner"], shards["set-1"], cache)["connected"]
    after = generations(config)
    assert after["set-1"] != first["set-1"] and after["set-2"] == first["set-2"]


def test_label_only_change_reaches_the_index_without_a_purge(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# Note\nMade-up fact.\n")
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    entry = {"pass": True, "sha256": sha, "description": "note", "question": "what is the fact?",
             "kind": "note", "status": "open", "as_of": "unknown", "subject": "facts"}
    idx = FileIndex("owner", tmp_path / "index.sqlite")
    idx.update("set-1", entries={str(f): entry}, walk=False)
    idx.update("set-1", entries={str(f): {**entry, "status": "done", "question": "which fact?"}}, walk=False)
    [(_ptr, _path, got)] = idx.candidates(["set-1"])
    assert (got["status"], got["question"]) == ("done", "which fact?")
