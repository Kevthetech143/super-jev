"""Read-only curated setup answers for the Super Jev dispatcher."""
import json
from pathlib import Path
import re


FAQ_PATH = Path(__file__).with_name("references") / "setup-faq.json"
_WORD = re.compile(r"[a-z0-9]+")


def _faq() -> list[dict]:
    """Load the bundled data only; setup help never opens user configuration."""
    return json.loads(FAQ_PATH.read_text(encoding="utf-8"))["topics"]


def menu() -> dict:
    return {
        "status": "ok",
        "kind": "builtin-help",
        "message": "Curated setup topics. Use help --topic ID for an exact answer, or help --question TEXT for possible topics.",
        "topics": [{"id": item["id"], "label": item["label"]} for item in _faq()],
    }


def _card(item: dict) -> dict:
    return {key: item[key] for key in ("id", "label", "answer", "sources")}


def topic(topic_id: str) -> dict:
    for item in _faq():
        if item["id"] == topic_id:
            return {"status": "ok", "kind": "builtin-help", "topic": _card(item)}
    return {
        "status": "no-covered-topic",
        "kind": "builtin-help",
        "reason": "unknown-topic-id",
        "topic": topic_id,
        "guide": "references/connectors.md",
    }


def suggestions(question: str) -> dict:
    """Suggest curated cards by phrase matching, not general question answering."""
    normalized = f" {' '.join(_WORD.findall(question.lower()))} "
    matches = []
    for item in _faq():
        if any(f" {' '.join(_WORD.findall(keyword.lower()))} " in normalized
               for keyword in item["matchPhrases"]):
            matches.append(_card(item))
    if matches:
        return {
            "status": "suggestions",
            "kind": "builtin-help",
            "message": "Possible static curated help cards, not a Jev call, general answer, or semantic-coverage guarantee.",
            "suggestions": matches[:3],
        }
    return {
        "status": "no-covered-topic",
        "kind": "builtin-help",
        "message": "No exact help match. Start with the Quick Start below; this is setup guidance, not an answer to your question.",
        "nextAction": "read-quick-start",
        "quickStart": topic("overview")["topic"],
        "guide": "references/connectors.md",
    }


def run(args: list[str]) -> tuple[int, dict]:
    if not args:
        return 0, menu()
    if len(args) == 2 and args[0] == "--topic":
        result = topic(args[1])
        return (0 if result["status"] == "ok" else 2), result
    if len(args) == 2 and args[0] == "--question":
        return 0, suggestions(args[1])
    return 2, {
        "status": "error",
        "kind": "builtin-help",
        "reason": "usage: help [--topic ID | --question TEXT]",
    }
