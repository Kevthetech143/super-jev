"""Measure synthetic full-snapshot freshness overhead for verified pointer cache hits.

This deliberately injects a local retrieval result and never calls Jev.  Its figures
measure file hashing, manifest checks, SQLite access, and verified-cache lookup for
small synthetic files; they do not measure Jev retrieval quality, accuracy, or
provider latency.
"""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from service import Service, pack, sha


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "scale-results.json"
SOURCE_BYTES = 4 * 1024
HITS_PER_SIZE = 10


def source_bytes(index: int) -> bytes:
    prefix = f"Synthetic reviewed policy {index}: the launch color is blue.\n".encode()
    return prefix + b"x" * (SOURCE_BYTES - len(prefix))


def measure(source_count: int) -> dict:
    """Build one reviewed corpus, approve one answer, then time exact cache hits."""
    with tempfile.TemporaryDirectory(dir=ROOT, prefix=".scale-probe-") as temporary:
        root = Path(temporary)
        sources = []
        originals = []
        for index in range(source_count):
            path = root / f"source-{index:04d}.txt"
            path.write_bytes(source_bytes(index))
            content_sha = sha(path)
            source = {
                "id": f"source-{index:04d}",
                "description": f"Synthetic reviewed source {index}",
                "path": str(path),
                "contentSHA": content_sha,
            }
            sources.append(source)
            originals.append({"path": str(path), "sha256": content_sha})

        citation = {
            "sourceId": sources[0]["id"], "path": sources[0]["path"],
            "contentSHA": sources[0]["contentSHA"], "startLine": 1, "endLine": 1,
            "reviewedText": source_bytes(0).decode(), "policy": "reviewed", "status": "reviewed",
        }
        manifest = root / "manifest.json"
        manifest.write_text(pack({
            "descriptionsAffirmed": True, "expectedPolicy": "reviewed",
            "sources": sources, "preparations": [citation],
        }))
        registry = root / "registry.json"
        registry.write_text(pack({"datasets": {"synthetic": {
            "description": "Synthetic reviewed scale-probe corpus",
            "manifestPath": str(manifest), "manifestSHA256": sha(manifest), "originals": originals,
        }}}))

        calls = 0

        def retrieve(dataset: str, question: str) -> dict:
            nonlocal calls
            calls += 1
            return {"status": "ready", "passages": [citation.copy()]}

        service = Service(root / "state.sqlite", registry, retrieve)
        service.register("synthetic", "synthetic", ["local"])
        ready = service.search("synthetic", "What is the launch color?", "local")
        if ready["status"] != "ready":
            raise AssertionError(ready)
        service.approve(
            ready["approvalTicket"], "local", "The launch color is blue.",
            [{"sourceId": citation["sourceId"], "quote": "blue"}], approved=True,
        )

        hit_ms = []
        for _ in range(HITS_PER_SIZE):
            start = time.perf_counter()
            hit = service.search("synthetic", "What is the launch color?", "local")
            hit_ms.append((time.perf_counter() - start) * 1000)
            if hit["status"] != "verified-cache-hit":
                raise AssertionError(hit)
        if calls != 1:
            raise AssertionError(f"expected one injected retrieval, got {calls}")

        # The pointer check must rehash this last file before it can return a cache hit.
        last = Path(sources[-1]["path"])
        last.write_bytes(b"changed after review\n" + b"y" * (SOURCE_BYTES - len(b"changed after review\n")))
        after_mutation = service.search("synthetic", "What is the launch color?", "local")
        if after_mutation["status"] == "verified-cache-hit" or calls != 1:
            raise AssertionError({"afterMutation": after_mutation, "providerCalls": calls})

        return {
            "sourceFiles": source_count,
            "bytesPerSource": SOURCE_BYTES,
            "unchangedCacheHits": HITS_PER_SIZE,
            "unchangedCacheHitFullSnapshotValidationMs": {
                "median": statistics.median(hit_ms), "max": max(hit_ms),
            },
            "providerCalls": calls,
            "afterLastSourceMutation": after_mutation["status"],
        }


def main() -> None:
    results = {
        "measurement": "synthetic freshness overhead, not Jev accuracy or provider performance",
        "method": "Each unchanged cache hit performs current Service full manifest/original/source hash validation.",
        "runs": [measure(count) for count in (1, 100, 1000)],
    }
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
