import json
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


DISPATCH = Path(__file__).resolve().parents[1] / "dispatch.py"
REFERENCES = DISPATCH.parent / "references"


def run(*args):
    return subprocess.run([sys.executable, str(DISPATCH), "help", *args], capture_output=True, text=True)


def response(*args):
    result = run(*args)
    return result, json.loads(result.stdout)


def test_help_menu_lists_curated_topics_without_setup():
    result, body = response()
    assert result.returncode == 0
    assert body["kind"] == "builtin-help"
    assert 8 <= len(body["topics"]) <= 10


def test_exact_topic_returns_curated_answer_and_sources():
    result, body = response("--topic", "register-setup")
    assert result.returncode == 0
    assert body["topic"]["answer"]
    assert "memory --describe" in body["topic"]["answer"]
    assert body["topic"]["sources"]


def test_setup_question_only_suggests_matching_topics():
    result, body = response("--question", "How do I connect my repo?")
    assert result.returncode == 0
    assert body["status"] == "suggestions"
    assert {item["id"] for item in body["suggestions"]} == {"register-setup"}
    assert body["suggestions"][0]["answer"]
    assert body["suggestions"][0]["sources"]


def test_manual_question_suggests_overview():
    result, body = response("--question", "How do I use SuperJev?")
    assert result.returncode == 0
    assert {item["id"] for item in body["suggestions"]} == {"overview"}
    assert "`skills`" in body["suggestions"][0]["answer"]


def test_unrelated_question_has_no_answer():
    result, body = response("--question", "What will the weather be tomorrow?")
    assert result.returncode == 0
    assert body["status"] == "no-covered-topic"
    assert body["guide"] == "references/connectors.md"


def test_missing_topic_and_unknown_arguments_fail_clearly():
    result, body = response("--topic", "not-real")
    assert result.returncode == 2
    assert body["reason"] == "unknown-topic-id"
    result, body = response("--unexpected")
    assert result.returncode == 2
    assert "usage" in body["reason"]


def test_all_document_references_exist():
    faq = json.loads((REFERENCES / "setup-faq.json").read_text())
    for topic in faq["topics"]:
        for source in topic["sources"]:
            assert (REFERENCES / source).resolve().is_file(), source


def test_help_needs_no_config_or_external_backend(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("setup_help_dispatch", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DISPATCH.parent))
    try:
        spec.loader.exec_module(module)
        monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("backend called"))
        assert module.main(["help", "--topic", "overview"]) == 0
    finally:
        sys.path.remove(str(DISPATCH.parent))
    assert json.loads(capsys.readouterr().out)["kind"] == "builtin-help"
