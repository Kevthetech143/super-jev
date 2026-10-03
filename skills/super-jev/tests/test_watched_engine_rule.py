"""Watched folders against the REAL engine (a temp config, never the live registry): the engine holds the one rule,
prepare_bulk only marks, leaves out what the engine would refuse, and reports it. Made-up names."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
ENGINE = SKILL.parent.parent / "experiments" / "verified-pointer-memory" / "cli.py"
SHIM = f'''#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
if a[:1] == ["memory"]:
    os.execv(sys.executable, [sys.executable, {str(ENGINE)!r}, "--config", os.environ["TEST_ENGINE_CONFIG"], "--input", "/dev/stdin"])
sys.exit(2)
'''


class WatchedEngineRule(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.skill = self.tmp / "skill"
        shutil.copytree(SKILL, self.skill, ignore=shutil.ignore_patterns(
            "tests", "prepare-cache", "autoheal-state", "__pycache__", ".pytest_cache"))
        (self.skill / "dispatch.py").write_text(SHIM)
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"db": str(self.tmp / "db.sqlite"), "registry": str(self.tmp / "registry.json")}))
        self.env = {"HOME": str(self.tmp / "home"), "PATH": os.environ["PATH"],  # the engine needs node
                    "TEST_ENGINE_CONFIG": str(cfg), "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        self.notes = self.tmp / "notes"
        for rel, text in {"garden/beds.md": "# Beds\n\nQuill plans the raised beds.\n",
                          "garden/seeds.md": "# Seeds\n\nQuill keeps the seed list.\n",
                          "other/tools.md": "# Tools\n\nThe spade lives in the shed.\n"}.items():
            (self.notes / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.notes / rel).write_text(text)

    def tearDown(self):
        self._tmp.cleanup()

    def run_pb(self, *args):
        return subprocess.run([sys.executable, str(self.skill / "prepare_bulk.py"), *args], cwd=self.skill,
                              env=self.env, capture_output=True, text=True, timeout=180)

    def connect(self, pointer, principal, root):
        return self.run_pb("--root", str(root), "--principal", principal, "--pointer", pointer, "--writer", "builtin",
                           "--no-findability")

    def refresh(self, pointer, principal):
        return self.run_pb("--pointer", pointer, "--principal", principal, "--refresh", "--writer", "builtin",
                           "--no-findability")

    def engine(self, body):
        r = subprocess.run([sys.executable, str(self.skill / "dispatch.py"), "memory", "--input", "/dev/stdin"],
                           input=json.dumps(body), env=self.env, capture_output=True, text=True)
        return json.loads(r.stdout)

    def engine_paths(self, principal):
        out = {}
        for p in self.engine({"action": "panel", "principal": principal})["pointers"]:
            srcs = self.engine({"action": "sources", "pointer": p["pointer"], "principal": principal})
            out[p["pointer"]] = sorted(Path(s["originalPath"]).name for s in srcs.get("sources", []))
        return out

    def test_enclosing_pointer_of_other_agents_keeps_working_and_only_watched_files_are_refused(self):
        self.assertEqual(self.connect("quill-garden", "quill", self.notes / "garden").returncode, 0)
        marked = self.run_pb("--watch", "--pointer", "quill-garden", "--principal", "quill")
        self.assertEqual(marked.returncode, 0, marked.stdout + marked.stderr)
        # N2: a rook pointer over the folder that ENCLOSES the watched one connects, minus the watched files.
        got = self.connect("rook-notes", "rook", self.notes)
        self.assertIn(got.returncode, (0, 3), got.stdout + got.stderr)
        self.assertIn("REFUSED", got.stdout)
        self.assertIn("quill-garden", got.stdout)
        self.assertEqual(self.engine_paths("rook"), {"rook-notes": ["tools.md"]})
        (self.notes / "other" / "more.md").write_text("# More\n\nRook adds a note.\n")
        (self.notes / "garden" / "new.md").write_text("# New\n\nQuill adds a bed.\n")
        again = self.refresh("rook-notes", "rook")
        self.assertIn(again.returncode, (0, 3), again.stdout + again.stderr)
        self.assertEqual(self.engine_paths("rook"), {"rook-notes": ["more.md", "tools.md"]})  # no freeze

    def test_other_agent_pointer_inside_a_watched_folder_is_refused_and_registers_nothing(self):
        self.assertEqual(self.connect("quill-garden", "quill", self.notes / "garden").returncode, 0)
        self.assertEqual(self.run_pb("--watch", "--pointer", "quill-garden", "--principal", "quill").returncode, 0)
        got = self.connect("rook-seeds", "rook", self.notes / "garden")
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("quill-garden", got.stdout)
        self.assertIn("--unwatch --pointer quill-garden", got.stdout)
        self.assertEqual(self.engine_paths("rook"), {})

    def test_engine_connect_without_the_helper_is_refused_too(self):  # N1
        self.assertEqual(self.connect("quill-garden", "quill", self.notes / "garden").returncode, 0)
        self.assertEqual(self.run_pb("--watch", "--pointer", "quill-garden", "--principal", "quill").returncode, 0)
        body = {"action": "connect", "pointer": "rook-direct", "principals": ["rook"],
                "sources": [{"path": str(self.notes / "garden" / "beds.md")}]}
        preview = self.engine(body)
        confirmed = self.engine({**body, "sources": preview.get("sources") or body["sources"], "reviewed": True})
        self.assertEqual(confirmed.get("reason"), "watched-refused", confirmed)
        self.assertEqual(self.engine_paths("rook"), {})

    def test_unwatch_clears_the_engines_mark(self):
        self.assertEqual(self.connect("quill-garden", "quill", self.notes / "garden").returncode, 0)
        self.assertEqual(self.run_pb("--watch", "--pointer", "quill-garden", "--principal", "quill").returncode, 0)
        self.assertEqual(self.run_pb("--unwatch", "--pointer", "quill-garden", "--principal", "quill").returncode, 0)
        self.assertIn(self.connect("rook-seeds", "rook", self.notes / "garden").returncode, (0, 3))


if __name__ == "__main__":
    unittest.main()
