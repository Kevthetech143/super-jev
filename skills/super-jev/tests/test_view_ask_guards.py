"""A previous bulk cache or raw claim verdict cannot bypass a view connector."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
spec = importlib.util.spec_from_file_location('view_guard_ask', SKILL / 'ask.py')
ask = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask)


def setup(tmp_path, monkeypatch, stale=False):
    raw = tmp_path / 'note.md'
    raw.write_text('Project public notes with PRIVATE_CANARY.\n')
    cache = tmp_path / 'cache'
    cache.mkdir()
    (cache / 'notes.json').write_text(json.dumps({str(raw): {
        'pass': True, 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'description': 'Project public notes'}}))
    monkeypatch.setenv('SUPERJEV_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setenv('SUPERJEV_BATCH_JEV', '0')
    monkeypatch.setenv('SUPERJEV_SKILLS', '0')
    monkeypatch.setattr(ask.prepare_bulk, 'CACHE_DIR', cache)
    def memory(req):
        if req['action'] == 'cached':
            return {'status': 'miss'}
        if req['action'] == 'panel':
            return {'pointers': [{'pointer': 'notes', 'viewOriginals': [str(raw)]}]}
        return {'status': 'preparation-required' if stale else 'no-candidates'}
    monkeypatch.setattr(ask, 'memory', memory)
    def refuse_bulk(*a, **k):
        raise AssertionError('raw bulk refresh reached')
    monkeypatch.setattr(ask.auto_heal, 'reconnect_now', refuse_bulk)
    monkeypatch.setattr(ask.auto_heal, 'maybe_heal', refuse_bulk)
    seen = []
    monkeypatch.setattr(ask, 'confirm', lambda q, paths: (seen.extend(paths) or {}, set(), None, {}))
    return raw, seen


def test_lookup_never_reads_an_old_raw_bulk_cache(tmp_path, monkeypatch):
    raw, seen = setup(tmp_path, monkeypatch)
    ask.lookup('Project public notes', 'owner', tmp_path / 'state/owner')
    assert str(raw) not in seen
    assert ask.load_cache_files('notes') == {}


def test_stale_view_only_attempts_its_recipe_not_old_bulk_report(tmp_path, monkeypatch):
    _, _ = setup(tmp_path, monkeypatch, stale=True)
    recipes = []
    monkeypatch.setattr(ask.auto_heal, 'heal_in_background',
                        lambda ptr, principal, view=False: recipes.append((ptr, view)) or 'started')
    ask.lookup('Project public notes', 'owner', tmp_path / 'state/owner')
    assert recipes == [('notes', True)]  # the recipe, started in the background; never the old bulk report


def test_saved_raw_claim_is_withheld_after_view_conversion(tmp_path, monkeypatch, capsys):
    raw, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setitem(ask._CLAIM, 'text', 'Project public notes')
    monkeypatch.setattr(ask, 'claim_cache_get', lambda *a: {
        'path': str(raw), 'verdict': 'TRUE', 'prob': 1, 'line_no': 1, 'line': 'PRIVATE_CANARY'})
    ask.lookup('Project public notes', 'owner', tmp_path / 'state/owner')
    assert 'PRIVATE_CANARY' not in capsys.readouterr().out
