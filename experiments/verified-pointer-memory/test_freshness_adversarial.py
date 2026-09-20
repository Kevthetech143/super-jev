"""Adversarial lifecycle checks for malformed-but-JSON cache inputs.

These tests use only synthetic files and an injected retrieval callback.  They
intentionally leave the implementation untouched when a bad state is accepted
or raises an unstructured exception.
"""

import json
import tempfile
import unittest
from pathlib import Path

from service import Service, pack, sha


TEST_TMP = Path(__file__).parent / ".test-tmp"
TEST_TMP.mkdir(exist_ok=True)


class SyntheticFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"
        self.manifest = self.root / "manifest.json"
        self.registry = self.root / "registry.json"
        self.db = self.root / "state.sqlite"
        self.calls = 0
        self.source.write_text("The launch color is blue.\n")
        content_hash = sha(self.source)
        passage = {
            "sourceId": "one", "path": str(self.source),
            "contentSHA": content_hash, "startLine": 1, "endLine": 1,
            "reviewedText": self.source.read_text(),
        }
        self.passage = passage
        self.manifest.write_text(pack({
            "sources": [{"id": "one", "sourceId": "one",
                         "path": str(self.source), "contentSHA": content_hash}],
            "preparations": [{**passage, "policy": "reviewed",
                               "status": "reviewed"}],
        }))
        self.registry.write_text(pack({"datasets": {"test": {
            "manifestPath": str(self.manifest),
            "manifestSHA256": sha(self.manifest),
            "originals": [{"path": str(self.source), "sha256": content_hash}],
        }}}))
        self.service = Service(self.db, self.registry, self.retrieve,
                               cache_ttl_seconds=1000)
        self.service.register("docs", "test", ["alice"])

    def retrieve(self, dataset, question):
        self.calls += 1
        return {"status": "ready", "passages": [self.passage.copy()]}

    def close(self):
        self.temp.cleanup()


class FreshnessAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.f = SyntheticFixture()
        self.addCleanup(self.f.close)

    def approved_cache(self):
        result = self.f.service.search("docs", "color?", "alice", now=100)
        self.f.service.approve(
            result["approvalTicket"], "alice", "Blue.",
            [{"sourceId": "one", "quote": "blue"}], approved=True, now=100,
        )

    def test_valid_json_cache_with_wrong_answer_shape_is_not_accepted(self):
        """A parseable cache row with unusable evidence must miss safely."""
        self.approved_cache()
        with self.f.service.connect() as db:
            db.execute("UPDATE cache SET body=?", (pack({
                "answer": {"forged": True}, "evidence": [], "expires": 10_000,
            }),))
        result = self.f.service.search("docs", "color?", "alice", now=101)
        self.assertNotEqual(result["status"], "verified-cache-hit")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.f.calls, 2)

    def test_valid_json_manifest_shape_failure_is_bounded_at_approval(self):
        """Malformed valid JSON must become a review error, not leak TypeError."""
        result = self.f.service.search("docs", "color?", "alice", now=100)
        self.f.manifest.write_text(pack({"sources": {"one": {
            "id": "one", "path": str(self.f.source),
            "contentSHA": sha(self.f.source),
        }}, "preparations": None}))
        with self.assertRaises(ValueError):
            self.f.service.approve(
                result["approvalTicket"], "alice", "Blue.",
                [{"sourceId": "one", "quote": "blue"}], approved=True, now=100,
            )


if __name__ == "__main__":
    unittest.main()
