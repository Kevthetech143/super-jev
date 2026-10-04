"""judges.key_file_path(): the one file-path rule the terminal app and the key provider follow."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import judges  # noqa: E402


def test_default_is_the_home_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(judges.key_env() + "_FILE", raising=False)
    assert judges.key_file_path() == os.path.join(str(tmp_path), ".typesafe-api-key")


def test_key_env_file_variable_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(judges.key_env() + "_FILE", str(tmp_path / "k"))
    assert judges.key_file_path() == str(tmp_path / "k")
