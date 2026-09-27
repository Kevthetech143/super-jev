"""Offline shadow proposal gates and deterministic source replay."""
import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import shadow_repair as sr


def test_accept_requires_every_gate():
    assert sr.decide(False, True, True, 1, True, True)[0] == 'ACCEPT'
    for args in [(True,True,True,1,True,True), (False,False,True,1,True,True),
                 (False,True,False,1,True,True), (False,True,True,0,True,True),
                 (False,True,True,1,False,True), (False,True,True,1,True,False)]:
        assert sr.decide(*args)[0] == 'REJECT'


def test_cause_never_invents_routing_failure():
    d={'target':{'gold':['/note.md']},'sources':{},'entries':{}}
    assert sr.classify(d,None,False)[0]=='missing-or-unavailable-note'
    d['sources']['/note.md']={'status':'captured','sha256':'new'}
    d['entries']={'p':{'/note.md':{'sha256':'old'}}}
    assert sr.classify(d,2,True)[0]=='stale-note'
    d['entries']['p']['/note.md']['sha256']='new'
    assert sr.classify(d,6,False)[0]=='search-ranking'
    assert sr.classify(d,2,True)[0]=='routing-or-content-unresolved'


def bundle(tmp_path):
    root=tmp_path/'frozen';root.mkdir();(root/'sources').mkdir();(root/'baseline').mkdir()
    note=tmp_path/'note.md';note.write_text('original')
    raw=note.read_bytes();sha=sr.digest(raw);(root/'sources'/sha).write_bytes(raw)
    # Fake build reads the original pathname, so editing that file must not affect replay.
    code='''from pathlib import Path
from types import SimpleNamespace
prepare_bulk=SimpleNamespace(CACHE_DIR=Path('.'))
FALLBACK_FILES=1
_STAGE={}
def word_search(question,pointers,limit=5):
    text=Path(%r).read_bytes()
    rows=[(1, %r, 'p')] if text==b'original' else []
    _STAGE['word']={'ranked':rows}
    return rows
''' % (str(note),str(note))
    (root/'baseline'/'ask.py').write_text(code)
    target={'question':'find note','gold':[str(note)],'principal':'reader','split':'tuned'}
    data=dict(target=target,cases=[target],pointers={'reader':['p']},entries={'p':{str(note):{'sha256':sha}}},
              names={'p':[]},admit={'p':{}},sources={str(note):{'status':'captured','blob':sha,'sha256':sha}},
              blobs={sha:sha},baseline={'entry':'ask.py','files':{'ask.py':sr.digest(code.encode())}},held_out_sha256=None)
    sr.write_json(root/'manifest.json',data)
    return root,sr.digest((root/'manifest.json').read_bytes()),note


def test_frozen_replay_ignores_live_note_change_and_never_edits_it(tmp_path):
    root,pin,note=bundle(tmp_path);note.write_text('changed')
    result=sr.compare(root,pin,root/'baseline'/'ask.py',tmp_path/'result')
    assert result['target_readable']==[True,True]
    assert result['decision']=='REJECT' and result['deployed'] is False
    assert note.read_text()=='changed'
    assert (tmp_path/'result'/'proposal.md').is_file()
    tool=tmp_path/'result'/'tools'/'shadow_repair.py'
    assert sr.digest(tool.read_bytes())==result['runner_sha256']


def test_modified_snapshot_refused_before_candidate_import(tmp_path):
    root,pin,_=bundle(tmp_path)
    (root/'manifest.json').write_text('{}')
    with pytest.raises(ValueError,match='checksum'):
        sr.compare(root,pin,tmp_path/'nonexistent.py',tmp_path/'result')
    assert not (tmp_path/'result').exists()


def test_modified_blob_refused(tmp_path):
    root,pin,_=bundle(tmp_path)
    next((root/'sources').iterdir()).write_text('changed')
    with pytest.raises(ValueError,match='source snapshot'):
        sr.validate_bundle(root,pin)


def test_source_scope_excludes_vaults_and_credentials():
    for p in ['/x/logins.md','/x/name-secret.md','/x/profile/note.md','/x/documents/note.md','/x/.env','relative.md']:
        assert not sr.allowed(p)
    assert sr.allowed('/x/docs/guide.md')


def test_read_slot_expansion_is_explicit_and_needs_paid_evidence(tmp_path):
    root,pin,note=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir()
    code=(root/'baseline'/'ask.py').read_text().replace('FALLBACK_FILES=1','FALLBACK_FILES=2')
    (candidate/'ask.py').write_text(code)
    r=sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')
    assert r['decision']=='REJECT'
    assert r['read_slots'] == [1,2]
    assert any('Read-slot budget changed' in x for x in r['uncertainty'])
    assert 'paid-stage evidence missing or not accepted' in r['reasons']


def test_duplicate_target_is_not_an_independent_control(tmp_path):
    c={'question':'one','gold':['/note.md'],'principal':'reader','split':'tuned'}
    with pytest.raises(ValueError,match='independent control'):
        sr.freeze(c,[c],None,None,'missing.py','missing-cache',tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()


def test_capture_uses_only_panel_metadata_and_preserves_reviewed_bytes(tmp_path):
    note=tmp_path/'note.md';note.write_text('reviewed')
    control=tmp_path/'control.md';control.write_text('control')
    build=tmp_path/'build';build.mkdir()
    entries={str(p):{'pass':True,'sha256':sr.digest(p.read_bytes())} for p in (note,control)}
    code='''from types import SimpleNamespace
from pathlib import Path
prepare_bulk=SimpleNamespace(CACHE_DIR=Path('.'),CEILING_BYTES=1000)
def memory(req):
    assert req=={'action':'panel','principal':'reader'}
    return {'pointers':[{'pointer':'notes'}]}
def load_cache_files(ptr): return %r
def connector_names(ptr): return []
def refresh_would_admit(path,ptr): return True
def has_secret(text): return False
''' % entries
    (build/'ask.py').write_text(code)
    (build/'secret_patterns.json').write_text('{}')
    target={'question':'target','principal':'reader','gold':[str(note)]}
    controls=[{'question':'control','principal':'reader','gold':[str(control)]}]
    pin=sr.freeze(target,controls,None,None,build/'ask.py',tmp_path/'cache',tmp_path/'snapshot')
    data=sr.validate_bundle(tmp_path/'snapshot',pin)
    assert data['sources'][str(note)]['sha256']==sr.digest(b'reviewed')
    assert data['cases'][0]['split']=='tuned' and data['cases'][1]['split']=='retrospective'
    assert 'secret_patterns.json' in data['baseline']['files']
    assert note.read_text()=='reviewed' and control.read_text()=='control'
