"""Real connector + recipe healer integration, no provider calls."""
import importlib.util
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL.parents[1] / 'experiments/verified-pointer-memory'))
from path_connect import connect
from service import Service
import auto_heal as ah


def test_heal_keeps_redactions_and_carries_both_view_review_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(ah, 'STATE_DIR', tmp_path / 'state')
    monkeypatch.setattr(ah, 'LOG_PATH', tmp_path / 'state/autoheal.log')
    src = tmp_path / 'note.txt'
    src.write_text('Alice Example wrote the first note.\n')
    policy = {'version': 1, 'operations': [{'op': 'redact-literals', 'values': ['Alice Example'],
                                          'replacement': '[PERSON_REDACTED]'}]}
    config = {'db': str(tmp_path / 'db.sqlite'), 'registry': str(tmp_path / 'registry.json')}
    service = Service(config['db'], config['registry'], lambda *_: None)
    req = {'pointer': 'notes', 'principals': ['agent'],
           'sources': [{'path': str(src), 'viewTransform': policy}]}
    preview = connect(req, config)
    reviewed = {**req, 'reviewed': True,
                'sources': [{**req['sources'][0], **preview['sources'][0]}]}
    assert connect(reviewed, config)['status'] == 'registered'
    src.write_text('Alice Example wrote a new note.\n')
    def memory(request):
        if request['action'] == 'recipe':
            return service.recipe(request['pointer'], request['principal'])
        return connect(request, config)
    assert ah.reconnect_recipe('notes', 'agent', memory=memory) == 'reconnected'
    row = service.sources('notes', 'agent')['sources'][0]
    assert Path(row['path']).read_text() == '[PERSON_REDACTED] wrote a new note.\n'
    assert service.recipe('notes', 'agent')['recipe']['sources'][0]['viewTransform'] == policy
