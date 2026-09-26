import sys
from pathlib import Path
SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import ask

def test_late_routed_file_found_by_word_search_under_other_spelling(tmp_path, monkeypatch, capsys):
    rel = tmp_path / "release"; rel.mkdir()
    for i in range(5):
        (rel / f"other{i}.md").write_text("x")
    target = rel / "SKILL.md"; target.write_text("x")
    link = tmp_path / "linked"; link.symlink_to(rel)
    cands = [{"score": 0.9 - i * 0.01, "originalPath": str(rel / f"other{i}.md")} for i in range(5)]
    cands.append({"score": 0.5, "originalPath": str(target)})
    def fake_memory(req):
        if req["action"] == "cached":
            return {"status": "cache-miss", "checked": []}
        if req["action"] == "panel":
            return {"pointers": [{"pointer": "p1"}]}
        if req["action"] == "navigate-many":
            return {"status": "error"}
        if req["action"] == "navigate":
            return {"status": "candidates", "candidates": cands}
        raise AssertionError(req)
    monkeypatch.setattr(ask, "memory", fake_memory)
    monkeypatch.setattr(ask, "word_search", lambda q, ptrs, skip=(), **k: [(9.0, str(link / "SKILL.md"), "p1")])
    seen = []
    def fake_confirm(q, paths):
        seen.extend(paths)
        return ({p: (0.95 if p.endswith("SKILL.md") else 0.0) for p in paths}, set(), None, {})
    monkeypatch.setattr(ask, "confirm", fake_confirm)
    ask.lookup("where is it?", "alice", tmp_path)
    out = capsys.readouterr().out
    assert any(p.endswith("SKILL.md") for p in seen), (seen, out)
