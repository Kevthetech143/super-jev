import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from service import Service, SCHEMA_VERSION, pack, sha


class Fixture:

    def __init__(self, **service_kwargs):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"
        self.manifest = self.root / "manifest.json"
        self.registry = self.root / "registry.json"
        self.db = self.root / "state.sqlite"
        self.calls = 0
        self.source.write_text("The launch color is blue.\n")
        content_hash = sha(self.source)
        self.passage = {
            "sourceId": "one",
            "path": str(self.source),
            "contentSHA": content_hash,
            "startLine": 1,
            "endLine": 1,
            "reviewedText": self.source.read_text(),
        }
        self.write_manifest(self.passage["reviewedText"])
        self.registry.write_text(
            pack({
                "datasets": {
                    "test": {
                        "manifestPath":
                        str(self.manifest),
                        "manifestSHA256":
                        sha(self.manifest),
                        "originals": [{
                            "path": str(self.source),
                            "sha256": content_hash
                        }],
                    }
                }
            }))
        self.service = Service(self.db, self.registry, self.retrieve,
                               **service_kwargs)
        self.service.register("docs", "test", ["alice"])

    def write_manifest(self, reviewed_text):
        self.manifest.write_text(
            pack({
                "sources": [{
                    key: self.passage[key]
                    for key in ("sourceId", "path", "contentSHA")
                } | {
                    "id": "one"
                }],
                "preparations": [{
                    **self.passage, "reviewedText": reviewed_text,
                    "policy": "reviewed",
                    "status": "reviewed"
                }],
            }))

    def retrieve(self, dataset, question):
        self.calls += 1
        return {"status": "ready", "passages": [self.passage.copy()]}

    def ticket(self, question="color?"):
        return self.service.search("docs", question, "alice")["approvalTicket"]

    def approve(self, ticket):
        self.service.approve(ticket,
                             "alice",
                             "Blue.", [{
                                 "sourceId": "one",
                                 "quote": "blue"
                             }],
                             approved=True)


class RegressionTests(unittest.TestCase):

    def fixture(self, **kwargs):
        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.temp.cleanup)
        return fixture

    def test_cache_hit_rechecks_pointer_in_transaction(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        original = fixture.service.pointer

        def revoke_after_validation(name, principal):
            pointer, error = original(name, principal)
            fixture.service.pointer = original
            with fixture.service.connect() as connection:
                connection.execute("DELETE FROM pointers WHERE name = ?",
                                   (name, ))
            return pointer, error

        fixture.service.pointer = revoke_after_validation
        calls = fixture.calls
        self.assertEqual(
            fixture.service.search("docs", "color?", "alice")["status"],
            "unknown-pointer")
        self.assertEqual(fixture.calls, calls)

    def test_approval_rejects_manifest_changed_after_pointer_check(self):
        fixture = self.fixture()
        ticket = fixture.ticket()
        original = fixture.service.pointer

        def forge_after_validation(name, principal):
            pointer, error = original(name, principal)
            fixture.service.pointer = original
            fixture.write_manifest(
                "The launch color is blue. Forged approval text.")
            return pointer, error

        fixture.service.pointer = forge_after_validation
        with self.assertRaisesRegex(ValueError, "stale manifest"):
            fixture.approve(ticket)

    def test_remove_and_reregister_clear_associated_rows(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        fixture.ticket("another question")
        fixture.service.register("docs", "test", ["alice"])
        with fixture.service.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM cache").fetchone()[0],
                0)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM pending").fetchone()
                [0], 0)
        fixture.ticket()
        fixture.service.remove("docs")
        with fixture.service.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM pending").fetchone()
                [0], 0)

    def test_rows_store_compact_bindings(self):
        fixture = self.fixture()
        ticket = fixture.ticket()
        with fixture.service.connect() as connection:
            pending = connection.execute(
                "SELECT body FROM pending WHERE ticket = ?",
                (ticket, )).fetchone()[0]
        self.assertNotIn('snapshot', json.loads(pending))
        fixture.approve(ticket)
        with fixture.service.connect() as connection:
            cached = connection.execute("SELECT body FROM cache").fetchone()[0]
        self.assertNotIn('snapshot', json.loads(cached))

    def test_invalid_retrieval_shape_is_structured_error(self):
        fixture = self.fixture()
        fixture.service.retrieve = lambda dataset, question: []
        result = fixture.service.search("docs", "new", "alice")
        self.assertIsInstance(result["attemptId"], str)
        self.assertEqual({k: result[k] for k in ("status", "reason")}, {
            "status": "error",
            "reason": "invalid-retrieval-result"
        })

    def test_ready_requires_usable_passages(self):
        fixture = self.fixture()
        invalid_passages = [
            [],
            [{**fixture.passage, "reviewedText": ""}],
            [{**fixture.passage, "startLine": "1"}],
            [{**fixture.passage, "startLine": 2, "endLine": 1}],
        ]
        for index, passages in enumerate(invalid_passages):
            with self.subTest(passages=passages):
                fixture.service.retrieve = (
                    lambda dataset, question, value=passages: {
                        "status": "ready", "passages": value
                    })
                result = fixture.service.search(
                    "docs", f"invalid {index}", "alice")
                self.assertIsInstance(result["attemptId"], str)
                self.assertEqual({k: result[k] for k in ("status", "reason")}, {
                    "status": "error",
                    "reason": "invalid-retrieval-result",
                })

    def test_configurable_ttls_and_validation(self):
        fixture = self.fixture(cache_ttl_seconds=10, review_ttl_seconds=5)
        start = 1000
        result = fixture.service.search("docs", "color?", "alice", now=start)
        with fixture.service.connect() as connection:
            body = json.loads(
                connection.execute("SELECT body FROM pending").fetchone()[0])
        self.assertEqual(body["expires"], start + 5)
        fixture.service.approve(result["approvalTicket"],
                                "alice",
                                "Blue.", [{
                                    "sourceId": "one",
                                    "quote": "blue"
                                }],
                                approved=True,
                                now=start)
        with fixture.service.connect() as connection:
            hit = json.loads(
                connection.execute("SELECT body FROM cache").fetchone()[0])
        self.assertEqual(hit["expires"], start + 10)
        with self.assertRaises(ValueError):
            Service(fixture.root / "bad.sqlite",
                    fixture.registry,
                    fixture.retrieve,
                    cache_ttl_seconds=float("inf"))

    def test_legacy_database_is_refused_without_mutation(self):
        fixture = self.fixture()
        legacy = fixture.root / "legacy.sqlite"
        with sqlite3.connect(legacy) as connection:
            connection.execute(
                "CREATE TABLE pointers(name TEXT PRIMARY KEY, body TEXT)")
        with self.assertRaisesRegex(ValueError, "create a new v2 database"):
            Service(legacy, fixture.registry, fixture.retrieve)
        with sqlite3.connect(legacy) as connection:
            self.assertEqual(
                {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")
                }, {"pointers"})

    def test_unsupported_schema_is_refused_without_mutation(self):
        fixture = self.fixture()
        unsupported = fixture.root / "unsupported.sqlite"
        with sqlite3.connect(unsupported) as connection:
            connection.execute("CREATE TABLE schema_meta(version TEXT NOT NULL)")
            connection.execute("INSERT INTO schema_meta VALUES ('1')")
            connection.execute("CREATE TABLE marker(value TEXT)")
            connection.execute("INSERT INTO marker VALUES ('keep')")
        with sqlite3.connect(unsupported) as connection:
            before = list(connection.iterdump())
        with self.assertRaisesRegex(ValueError, f"expected version {SCHEMA_VERSION}"):
            Service(unsupported, fixture.registry, fixture.retrieve)
        with sqlite3.connect(unsupported) as connection:
            self.assertEqual(list(connection.iterdump()), before)

    def test_concurrent_constructors_create_one_version_row(self):
        fixture = self.fixture()
        concurrent_db = fixture.root / "concurrent.sqlite"

        def construct(_):
            Service(concurrent_db, fixture.registry, fixture.retrieve)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(construct, range(8)))
        with sqlite3.connect(concurrent_db) as connection:
            rows = connection.execute("SELECT version FROM schema_meta").fetchall()
        self.assertEqual(rows, [(SCHEMA_VERSION,)])


if __name__ == "__main__":
    unittest.main()
