"""Prepared view content must be pinned before a paid replay can read it."""
import importlib.util
import json
import os
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
import paid_replay as replay


def test_replay_pins_both_upstream_and_prepared_view(tmp_path):
    memory = tmp_path / 'memory'
    memory.mkdir()
    original = tmp_path / 'original.txt'
    original.write_text('Private text')
    view = tmp_path / 'view.txt'
    view.write_text('[REDACTED]')
    manifest = memory / 'manifest.json'
    manifest.write_text(json.dumps({'sources': [{'path': str(view)}]}))
    (memory / 'registry.json').write_text(json.dumps({'datasets': {'notes': {
        'originals': [{'path': str(original)}], 'manifestPath': str(manifest)}}}))
    assert replay.eligible_sources(tmp_path, tmp_path / 'cache') == {
        os.path.realpath(original), os.path.realpath(view)}
