import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from file_index import StatSha  # noqa: E402


def _old_file(tmp_path, name, text="x"):
    f = tmp_path / name
    f.write_text(text)
    os.utime(f, (1, 1))  # not racy, so put() keeps it
    return f


def test_lookup_by_key_hit_miss_and_close(tmp_path):
    f = _old_file(tmp_path, "a.txt")
    db = tmp_path / "memo.sqlite"
    memo = StatSha(db)
    memo.RACY_NS = 0  # a file just written has a fresh ctime; let put() keep it
    k, sha = memo.get(str(f))
    assert sha is None and memo.db is None  # no memo file yet
    memo.put(str(f), k, "abc")
    memo.flush()
    fresh = StatSha(db)
    assert fresh.get(str(f))[1] == "abc"
    assert fresh.db is not None
    fresh.close()
    assert fresh.db is None
    assert fresh.get(str(f))[1] == "abc"  # reopens
    fresh.flush()
    assert fresh.db is None  # flush closes the read connection


def test_changed_file_is_not_a_hit(tmp_path):
    f = _old_file(tmp_path, "b.txt")
    db = tmp_path / "memo.sqlite"
    m = StatSha(db)
    m.RACY_NS = 0
    k, _ = m.get(str(f))
    m.put(str(f), k, "abc")
    m.flush()
    f.write_text("longer content")
    assert StatSha(db).get(str(f))[1] is None
