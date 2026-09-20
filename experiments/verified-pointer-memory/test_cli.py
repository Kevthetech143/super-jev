"""Public CLI and bundled retrieval bridge checks using only synthetic data."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "cli.py"
BRIDGE = ROOT / "retrieve.ts"
TEMP_ROOT = ROOT / ".test-tmp"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PublicCliTests(unittest.TestCase):
    """Exercise the documented interface without a private config or API key."""

    @classmethod
    def setUpClass(cls):
        TEMP_ROOT.mkdir(exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        process = subprocess.run(
            [sys.executable, str(CLI), *args], text=True, capture_output=True,
            cwd=ROOT, check=False,
        )
        self.assertTrue(process.stdout, process.stderr)
        return process, json.loads(process.stdout)

    def write_json(self, name: str, body: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(body))
        return path

    def dataset(self, *, reviewed: bool = True, stale: bool = False) -> tuple[Path, Path]:
        source = self.root / "reviewed-policy.txt"
        source.write_text("The synthetic launch policy is blue.\n")
        source_hash = digest(source)
        manifest = self.root / "reviewed-manifest.json"
        source_record = {
            "id": "policy", "description": "Synthetic reviewed launch policy",
            "path": str(source), "contentSHA": source_hash,
        }
        manifest_body = {
            "descriptionsAffirmed": reviewed,
            "expectedPolicy": "reviewed" if reviewed else "unreviewed",
            "sources": [source_record],
            "preparations": [{
                **source_record, "sourceId": "policy", "startLine": 1, "endLine": 1,
                "reviewedText": source.read_text(), "policy": "reviewed", "status": "reviewed",
            }],
        }
        manifest.write_text(json.dumps(manifest_body))
        registry = self.root / "registry.json"
        registry.write_text(json.dumps({"datasets": {"synthetic-reviewed": {
            "description": "A synthetic reviewed dataset for public CLI tests",
            "manifestPath": str(manifest), "manifestSHA256": digest(manifest),
            "originals": [{"path": str(source), "sha256": source_hash}],
        }}}))
        if stale:
            source.write_text("The synthetic launch policy changed after review.\n")
        return registry, manifest

    def config(self, registry: Path, command: list[str] | None = None, **settings: object) -> Path:
        body: dict[str, object] = {
            "db": str(self.root / "state.sqlite"), "registry": str(registry),
            "cacheTtlSeconds": 17, "reviewTtlSeconds": 3, "providerTimeoutSeconds": 2,
        }
        if command is not None:
            body["retrievalCommand"] = command
        body.update(settings)
        return self.write_json("config.json", body)

    def provider(self, response: str, exit_code: int = 0) -> tuple[list[str], Path]:
        counter = self.root / "provider-calls.txt"
        counter.unlink(missing_ok=True)
        script = self.root / "trusted-provider.py"
        script.write_text(
            "from pathlib import Path\nimport sys\n"
            f"counter = Path({str(counter)!r})\n"
            "counter.write_text((counter.read_text() if counter.exists() else '') + 'call\\n')\n"
            f"sys.stdout.write({response!r})\n"
            f"raise SystemExit({exit_code})\n"
        )
        return [sys.executable, str(script)], counter

    def register(self, config: Path) -> None:
        request = self.write_json("register.json", {
            "action": "register", "pointer": "public-docs", "dataset": "synthetic-reviewed",
            "principals": ["alice"],
        })
        process, result = self.cli("--config", str(config), "--input", str(request))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["status"], "registered")

    def search(self, config: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        request = self.write_json("search.json", {
            "pointer": "public-docs", "question": "What is the launch policy?", "principal": "alice",
        })
        return self.cli("--config", str(config), "--input", str(request))

    def test_describe_needs_no_config_or_key(self):
        process, result = self.cli("--describe")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["nextAction"], "choose-action")
        self.assertIn("register", result["actions"])
        self.assertIn("cacheTtlSeconds", result["settings"])
        self.assertIn("Reviewed local dataset", result["requirements"])

    def test_unknown_config_is_a_structured_error(self):
        path = self.write_json("unknown-config.json", {"db": "state.sqlite", "registry": "registry.json", "surprise": True})
        process, result = self.cli("--config", str(path))
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result, {"status": "error", "reason": "Unsupported configuration setting.", "nextAction": "record-error"})

    def test_invalid_input_json_is_a_structured_error(self):
        registry, _ = self.dataset()
        config = self.config(registry)
        bad_input = self.root / "bad-input.json"
        bad_input.write_text("{not json")
        process, result = self.cli("--config", str(config), "--input", str(bad_input))
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "JSONDecodeError")
        self.assertEqual(result["nextAction"], "record-error")

    def test_synthetic_dataset_registers_once_and_panel_exposes_settings(self):
        registry, manifest = self.dataset()
        source = json.loads(manifest.read_text())["sources"][0]
        command, calls = self.provider(json.dumps({"status": "ready", "passages": [{
            "sourceId": "policy", "path": source["path"], "contentSHA": source["contentSHA"],
            "startLine": 1, "endLine": 1, "reviewedText": "The synthetic launch policy is blue.\n",
        }]}))
        config = self.config(registry, command)
        self.register(config)
        panel, result = self.cli("--config", str(config), "--principal", "alice")
        self.assertEqual(panel.returncode, 0, panel.stderr)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["settings"], {"cacheTtlSeconds": 17, "reviewTtlSeconds": 3, "providerTimeoutSeconds": 2})
        self.assertIn("context", result["optionalSearchFields"])
        self.assertEqual(len(result["pointers"]), 1)
        pointer = result["pointers"][0]
        self.assertEqual(pointer["pointer"], "public-docs")
        self.assertEqual(pointer["dataset"], "synthetic-reviewed")
        self.assertEqual(pointer["status"], "available")
        self.assertEqual(pointer["snapshotStatus"], "available")
        self.assertIsInstance(pointer["generation"], str)
        self.assertEqual(result["datasets"], [{"name": "synthetic-reviewed", "description": "A synthetic reviewed dataset for public CLI tests"}])
        search_started = time.time()
        search, ready = self.search(config)
        self.assertEqual(search.returncode, 0, search.stderr)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(calls.read_text().splitlines(), ["call"])
        with sqlite3.connect(self.root / "state.sqlite") as connection:
            pending = json.loads(connection.execute("SELECT body FROM pending").fetchone()[0])
        self.assertGreater(pending["expires"] - search_started, 2)
        self.assertLess(pending["expires"] - search_started, 4)
        approval = self.write_json("approve.json", {
            "action": "approve", "ticket": ready["approvalTicket"], "principal": "alice", "approved": True,
            "answer": "The launch policy is blue.", "evidence": [{"sourceId": "policy", "quote": "blue"}],
        })
        approval_started = time.time()
        approved, saved = self.cli("--config", str(config), "--input", str(approval))
        self.assertEqual(approved.returncode, 0, approved.stderr)
        self.assertEqual(saved["status"], "saved")
        with sqlite3.connect(self.root / "state.sqlite") as connection:
            cached = json.loads(connection.execute("SELECT body FROM cache").fetchone()[0])
        self.assertGreater(cached["expires"] - approval_started, 16)
        self.assertLess(cached["expires"] - approval_started, 18)

    def test_bad_provider_outputs_become_structured_errors(self):
        for label, response, exit_code, reason in (
            ("list", "[]", 0, "Retrieval failed or returned invalid JSON."),
            ("scalar", '"ready"', 0, "Retrieval failed or returned invalid JSON."),
            ("unknown-status", '{"status":"unexpected"}', 0, "Retrieval failed or returned invalid JSON."),
            ("nonzero", '{"status":"ready"}', 7, "Retrieval command failed."),
        ):
            with self.subTest(label=label):
                registry, _ = self.dataset()
                command, calls = self.provider(response, exit_code)
                config = self.config(registry, command)
                self.register(config)
                process, result = self.search(config)
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(result, {"status": "error", "reason": reason, "nextAction": "record-error"})
                self.assertEqual(calls.read_text().splitlines(), ["call"])

    def test_bundled_bridge_rejects_unreviewed_or_stale_data_without_key(self):
        for label, kwargs in (("unreviewed", {"reviewed": False}), ("stale", {"stale": True})):
            with self.subTest(label=label):
                registry, _ = self.dataset(**kwargs)
                process = subprocess.run(
                    ["node", str(BRIDGE)],
                    input=json.dumps({"registry": str(registry), "dataset": "synthetic-reviewed", "question": "Does not matter"}),
                    text=True, capture_output=True, cwd=ROOT, env={"PATH": os.environ["PATH"]}, check=False,
                )
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result, {
                    "status": "preparation-required",
                    "reason": "Missing, stale or unreviewed dataset; no provider call made.",
                })


if __name__ == "__main__":
    unittest.main()
