"""Privacy and freshness contracts for replayable reviewed views; entirely offline."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from path_connect import connect, prepare_bulk
from service import Service


POLICY = {'version': 1, 'operations': [
    {'op': 'drop-lines-containing', 'values': ['PRIVATE:']},
    {'op': 'redact-literals', 'values': ['Alice Example'], 'replacement': '[PERSON_REDACTED]'},
    {'op': 'redact-local-paths'}, {'op': 'redact-emails'}]}


@pytest.fixture
def setup(tmp_path):
    src = tmp_path / 'note.md'
    src.write_text('# Public\nAlice Example keeps /home/alice/notes.\nPRIVATE: hide this line\nContact alice@example.test\n')
    config = {'db': str(tmp_path / 'db.sqlite'), 'registry': str(tmp_path / 'registry.json')}
    req = {'pointer': 'notes', 'principals': ['owner'],
           'sources': [{'path': str(src), 'description': 'Public notes', 'viewTransform': copy.deepcopy(POLICY)}]}
    return src, config, req


def confirm(req, config):
    preview = connect(req, config)
    assert preview.get('reason') == 'review-required', preview
    return {**req, 'reviewed': True, 'navigationSHA': preview['navigationSHA'],
            'sources': [{**s, **p} for s, p in zip(req['sources'], preview['sources'])]}


def manifest(config):
    entry = json.loads(Path(config['registry']).read_text())['datasets']['notes']
    return json.loads(Path(entry['manifestPath']).read_text()), entry


def test_only_derived_bytes_and_offsets_are_published(setup):
    src, config, req = setup
    raw = src.read_bytes()
    preview = connect(req, config)
    assert 'Alice Example' not in json.dumps(preview)
    assert connect(confirm(req, config), config)['status'] == 'registered'
    m, entry = manifest(config)
    view = Path(m['sources'][0]['path']).read_text()
    assert 'Alice Example' not in view and 'PRIVATE:' not in view and '/home/alice' not in view
    assert '[PERSON_REDACTED]' in view and '[EMAIL_REDACTED]' in view
    assert src.read_bytes() == raw
    assert entry['originals'][0]['sha256'] == hashlib.sha256(raw).hexdigest()
    for p in m['preparations']:
        assert p['contentSHA'] == hashlib.sha256(view.encode()).hexdigest()
        assert p['reviewedText'] == '\n'.join(view.split('\n')[p['startLine'] - 1:p['endLine']])


@pytest.mark.parametrize('field', ['sha256', 'viewSHA', 'transformSHA'])
def test_each_review_binding_is_required(setup, field):
    _, config, req = setup
    reviewed = confirm(req, config)
    reviewed['sources'][0][field] = 'wrong'
    assert connect(reviewed, config)['reason'] == 'review-required'
    assert not Path(config['registry']).exists()


def test_lost_policy_on_preview_round_trip_never_becomes_raw(setup):
    _, config, req = setup
    preview = connect(req, config)
    assert connect({**req, 'sources': preview['sources'], 'reviewed': True}, config)['reason'] == 'invalid-view-transform'


def test_refresh_uses_original_freshness_and_recipe_policy(setup):
    src, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    service = Service(config['db'], config['registry'], lambda *_: None)
    src.write_text(src.read_text() + 'Alice Example added a new fact.\n')
    assert service.pointer('notes', 'owner')[1]['status'] == 'preparation-required'
    recipe = service.recipe('notes', 'owner')['recipe']
    assert recipe['sources'][0]['viewTransform'] == POLICY
    assert connect(confirm({**recipe, 'replace': True}, config), config)['status'] == 'registered'
    m, _ = manifest(config)
    assert '[PERSON_REDACTED] added a new fact.' in Path(m['sources'][0]['path']).read_text()
    assert service.pointer('notes', 'owner')[1] is None
    assert service.recipe('notes', 'outsider')['status'] == 'access-denied'


@pytest.mark.parametrize('mode', ['remove', 'change', 'rename-id'])
def test_existing_policy_cannot_be_removed_or_changed(setup, mode):
    _, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    before = Path(config['registry']).read_bytes()
    req['replace'] = True
    if mode == 'change':
        req['sources'][0]['viewTransform']['operations'] = [{'op': 'redact-emails'}]
    else:
        del req['sources'][0]['viewTransform']
        if mode == 'rename-id':
            req['sources'][0]['id'] = 'different-id'
    assert connect(confirm(req, config), config)['reason'] == 'view-policy-change'
    assert Path(config['registry']).read_bytes() == before


def test_manifest_fallback_retains_policy(setup):
    _, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    data = json.loads(Path(config['registry']).read_text())
    del data['datasets']['notes']['recipe']
    Path(config['registry']).write_text(json.dumps(data))
    service = Service(config['db'], config['registry'], lambda *_: None)
    assert service.recipe('notes', 'owner')['recipe']['sources'][0]['viewTransform'] == POLICY


def test_navigation_and_sources_never_direct_readers_to_raw_original(setup):
    src, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    service = Service(config['db'], config['registry'], lambda *_: None)
    begun = service._navigate_begin('notes', 'owner', 'public notes', None)
    leaf = next(n for n in begun['catalog']['nodes'] if 'sourceId' in n)
    result = service._navigate_end('notes', 'owner', begun, {
        'status': 'candidates', 'calls': 1, 'trace': [], 'complete': False, 'message': 'read',
        'candidates': [{'sourceId': leaf['sourceId'], 'nodeId': leaf['id'],
                        'path': ['root', leaf['id']], 'score': 0.9}]})
    nav = result['candidates'][0]
    listed = service.sources('notes', 'owner')['sources'][0]
    for row in (nav, listed):
        assert row['originalPath'] != str(src)
        assert row['upstreamPath'] == str(src)
        assert 'Alice Example' not in Path(row['originalPath']).read_text()


@pytest.mark.parametrize('policy', [None, {'version': 2, 'operations': []},
    {'version': 1, 'operations': [{'op': 'shell', 'command': 'cat original'}]},
    {'version': 1, 'operations': [{'op': 'redact-literals', 'values': [''], 'replacement': '[REDACTED]'}]}])
def test_invalid_transforms_fail_without_raw_fallback(setup, policy):
    _, config, req = setup
    req['sources'][0]['viewTransform'] = policy
    assert connect(req, config)['reason'] == 'invalid-view-transform'
    assert not Path(config['registry']).exists()


def test_original_edit_after_review_is_not_published(setup):
    src, config, req = setup
    reviewed = confirm(req, config)
    src.write_text(src.read_text() + 'Changed after preview\n')
    assert connect(reviewed, config)['reason'] == 'review-required'


def test_unredacted_secret_still_held(setup):
    src, config, req = setup
    src.write_text('password: this-is-a-private-password\nPublic note\n')
    assert connect(req, config)['reason'] == 'secret-held'
    req['sources'][0]['viewTransform'] = {'version': 1, 'operations': [
        {'op': 'drop-lines-containing', 'values': ['password:']}]}
    assert connect(confirm(req, config), config)['status'] == 'registered'
    m, _ = manifest(config)
    assert Path(m['sources'][0]['path']).read_text() == 'Public note\n'


def test_legacy_handmade_dataset_cannot_be_replaced_by_raw(setup):
    _, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    data = json.loads(Path(config['registry']).read_text())
    for field in ('pathConnection', 'recipe', 'viewPolicies'):
        data['datasets']['notes'].pop(field)
    Path(config['registry']).write_text(json.dumps(data))
    req['replace'] = True
    del req['sources'][0]['viewTransform']
    assert connect(confirm(req, config), config)['reason'] == 'legacy-view-policy-required'


@pytest.mark.parametrize('field', ['description', 'navigationPath', 'id'])
def test_hidden_literal_in_routing_metadata_is_refused(setup, field):
    _, config, req = setup
    if field == 'navigationPath':
        req['structure'] = 'folder-tree'
        req['sources'][0][field] = ['Alice Example']
    else:
        req['sources'][0][field] = 'Alice Example private notes'
    assert connect(req, config)['reason'] == 'view-metadata-held'
    assert not Path(config['registry']).exists()


def test_transformed_catalog_omits_raw_filename_and_parent_labels(setup):
    src, config, req = setup
    named = src.parent / 'Alice-Example' / 'Alice-Example-notes.md'
    named.parent.mkdir()
    named.write_bytes(src.read_bytes())
    req['sources'][0]['path'] = str(named)
    req['structure'] = 'folder-tree'
    preview = connect(req, config)
    assert 'Alice' not in json.dumps(preview['catalog'])
    assert connect(confirm(req, config), config)['status'] == 'registered'
    m, _ = manifest(config)
    assert 'Alice' not in json.dumps(m['catalog'])


@pytest.mark.parametrize('separator', ['\u2028', '\u0085'])
def test_unicode_separator_cannot_detach_text_from_its_drop_marker(setup, separator):
    src, config, req = setup
    src.write_text('secret-ish Jane Roe' + separator + 'PRIVATE: x\nPublic notes\n')
    assert connect(confirm(req, config), config)['status'] == 'registered'
    m, _ = manifest(config)
    assert Path(m['sources'][0]['path']).read_text() == 'Public notes\n'


@pytest.mark.parametrize('suffix', ['.json', '-report.json'])
def test_raw_bulk_artifact_blocks_view_conversion(setup, monkeypatch, suffix):
    _, config, req = setup
    cache = Path(config['db']).parent / 'cache'
    cache.mkdir()
    (cache / ('notes' + suffix)).write_text('{}')
    monkeypatch.setattr(prepare_bulk, 'CACHE_DIR', cache)
    assert connect(req, config)['reason'] == 'view-bulk-cache-conflict'
    assert not Path(config['registry']).exists()


def test_panel_keeps_view_guard_when_current_registry_policy_is_missing(setup):
    import cli
    src, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    cfg = Path(config['db']).with_suffix('.json')
    cfg.write_text(json.dumps(config))
    loaded = cli.load_config(cfg)
    panel = cli.run({'action': 'panel', 'principal': 'owner'}, loaded)
    assert panel['pointers'][0]['viewOriginals'] == [str(src)]
    registry = json.loads(Path(config['registry']).read_text())
    del registry['datasets']['notes']['viewPolicies']
    Path(config['registry']).write_text(json.dumps(registry))
    panel = cli.run({'action': 'panel', 'principal': 'owner'}, loaded)
    assert panel['pointers'][0]['viewOriginals'] == [str(src)]
    assert cli.run({'action': 'panel', 'principal': 'outsider'}, loaded)['pointers'] == []


def test_fallback_catalog_uses_opaque_view_label(setup, monkeypatch):
    _, config, req = setup
    assert connect(confirm(req, config), config)['status'] == 'registered'
    m, _ = manifest(config)
    del m['catalog']
    m['sources'][0]['originalPath'] = '/synthetic/Alice-Example-notes.md'
    service = Service(config['db'], config['registry'], lambda *_: None)
    binding = service.pointer('notes', 'owner')
    monkeypatch.setattr(service, 'pointer', lambda *_: binding)
    monkeypatch.setattr(service, '_manifest', lambda _: m)
    begun = service._navigate_begin('notes', 'owner', 'notes', None)
    assert 'Alice' not in json.dumps(begun['catalog'])


@pytest.mark.parametrize('principal,blocked', [('owner', True), ('outsider', False)])
@pytest.mark.parametrize('answer', ['Alice Example', 'PRIVATE: a saved line', 'Alice \"Example\"', r'Alice\Example'])
def test_manual_answer_with_hidden_literal_blocks_only_its_scope(setup, principal, blocked, answer):
    src, config, req = setup
    req['sources'][0]['viewTransform']['operations'][1]['values'].extend(['Alice \"Example\"', r'Alice\Example'])
    manual = src.with_name('manual.md')
    manual.write_text('An independently saved answer mentions ' + answer + '.\n')
    manual_req = {'pointer': principal + '-manual-abc123', 'principals': [principal],
                  'sources': [{'path': str(manual), 'description': 'Saved answer'}]}
    assert connect(confirm(manual_req, config), config)['status'] == 'registered'
    result = connect(req, config)
    assert result['reason'] == ('view-manual-record-conflict' if blocked else 'review-required')
    if blocked:
        assert result['manualPointers'] == ['owner-manual-abc123']
        assert 'Alice' not in json.dumps(result)


@pytest.mark.parametrize('shared', [False, True])
def test_switch_retires_owned_generation_and_raw_claim_proofs(setup, monkeypatch, shared):
    src, config, req = setup
    monkeypatch.setenv('SUPERJEV_STATE_DIR', str(src.parent / 'state'))
    raw_req = copy.deepcopy(req)
    del raw_req['sources'][0]['viewTransform']
    assert connect(confirm(raw_req, config), config)['status'] == 'registered'
    _, entry = manifest(config)
    old_folder = Path(entry['manifestPath']).parent
    if shared:
        registry = json.loads(Path(config['registry']).read_text())
        registry['datasets']['other-retained'] = copy.deepcopy(entry)
        Path(config['registry']).write_text(json.dumps(registry))
    claims = src.parent / 'state/owner/claim-verdicts.json'
    claims.parent.mkdir(parents=True)
    claims.write_text(json.dumps({'raw': {'path': str(src), 'line': 'Alice Example'},
                                 'unrelated': {'path': str(src.with_name('unrelated.md'))}}))
    req['replace'] = True
    result = connect(confirm(req, config), config)
    assert result['status'] == 'registered'
    assert old_folder.exists() == shared
    assert set(json.loads(claims.read_text())) == {'unrelated'}
    assert src.exists()
    assert bool(result['cleanupWarnings']) == shared


def test_unexpected_file_prevents_generation_deletion(setup, monkeypatch):
    src, config, req = setup
    monkeypatch.setenv('SUPERJEV_STATE_DIR', str(src.parent / 'state'))
    raw_req = copy.deepcopy(req)
    del raw_req['sources'][0]['viewTransform']
    assert connect(confirm(raw_req, config), config)['status'] == 'registered'
    _, entry = manifest(config)
    unexpected = Path(entry['manifestPath']).parent / 'operator-note.txt'
    unexpected.write_text('Keep this operator artifact')
    req['replace'] = True
    result = connect(confirm(req, config), config)
    assert result['status'] == 'registered'
    assert unexpected.read_text() == 'Keep this operator artifact'
    assert result['cleanupWarnings']
