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
    assert 'unfrozen file access attempted' not in result['reasons']
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


@pytest.mark.parametrize('read', ["Path(%r).read_text().encode()", "open(%r, 'rb').read()",
                                  "__import__('io').open(%r, 'rb').read()",
                                  "open(__import__('os').path.realpath(%r), 'rb').read()"])
def test_alternate_live_reads_refused(tmp_path, read):
    root,pin,note=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir()
    code=(root/'baseline'/'ask.py').read_text().replace("Path(%r).read_bytes()" % str(note), read % str(note))
    (candidate/'ask.py').write_text(code)
    note.write_text('changed')
    with pytest.raises(PermissionError, match='unfrozen'):
        sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')


def test_unknown_manifest_path_refused(tmp_path):
    root,pin,note=bundle(tmp_path)
    extra=tmp_path/'unrecorded.md';extra.write_text('original')
    candidate=tmp_path/'candidate';candidate.mkdir()
    (candidate/'ask.py').write_text((root/'baseline'/'ask.py').read_text().replace(str(note),str(extra)))
    with pytest.raises(PermissionError, match='unfrozen'):
        sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')


def test_baseline_unreadable_controls_and_heldout_cannot_satisfy_gates(tmp_path):
    root,pin,_=bundle(tmp_path)
    d=sr.validate_bundle(root,pin)
    control={'question':'control','split':'retrospective'}
    held={'question':'held','split':'held-out'}
    d['cases'] += [control,held];d['reserved']=[held]
    for reads in [[[False,True],[False,False],[True,True]],
                  [[False,True],[True,True],[False,True]]]:
        controls_ok,count,held_ok=sr.evidence_gates(d,reads,0)
        assert sr.decide(False,True,controls_ok,count,held_ok,True)[0]=='REJECT'


def test_only_reserved_rows_count_as_heldout(tmp_path):
    root,pin,_=bundle(tmp_path)
    d=sr.validate_bundle(root,pin)
    d['cases'] += [{'question':'control','split':'retrospective'},
                   {'question':'pretend','split':'held-out'}]
    d['reserved']=[{'question':'real reservation','split':'held-out'}]
    assert sr.evidence_gates(d,[[False,True],[True,True],[True,True]],0)[1]==0


def test_hand_labelled_heldout_rejected_even_with_reservation(tmp_path):
    reserved={'question':'reserved','gold':['/reserved.md'],'principal':'reader','split':'held-out',
              'group':'reserved','source_family':'reserved'}
    manifest=tmp_path/'held.json';sr.write_json(manifest,{'version':1,'cases':[reserved]})
    target={'question':'target','gold':['/target.md'],'principal':'reader'}
    control={'question':'pretend','gold':['/pretend.md'],'principal':'reader','split':'held-out'}
    with pytest.raises(ValueError,match='held-out input differs'):
        sr.freeze(target,[control],manifest,sr.digest(manifest.read_bytes()),'missing.py','cache',tmp_path/'snapshot')


def test_dependency_changes_appear_in_patch(tmp_path):
    root,pin,_=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir();(candidate/'lib').mkdir()
    (candidate/'ask.py').write_bytes((root/'baseline'/'ask.py').read_bytes())
    (candidate/'lib'/'repair.py').write_text('REPAIRED = True\n')
    sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')
    diff=(tmp_path/'result'/'candidate.patch').read_text()
    assert 'b/skills/super-jev/lib/repair.py' in diff and '+REPAIRED = True' in diff


def test_unlisted_snapshot_code_refused(tmp_path):
    root,pin,_=bundle(tmp_path)
    (root/'baseline'/'unlisted.py').write_text('EXTRA = True')
    with pytest.raises(ValueError,match='unlisted'):
        sr.validate_bundle(root,pin)


def test_replay_does_not_write_bytecode_into_snapshot(tmp_path):
    root,pin,_=bundle(tmp_path)
    sr.compare(root,pin,root/'baseline'/'ask.py',tmp_path/'result')
    assert not list(root.rglob('__pycache__'))
    sr.validate_bundle(root,pin)


@pytest.mark.parametrize("check_copy", [True, False])
def test_freeze_imports_hashed_copy_and_omits_excluded_metadata(tmp_path, monkeypatch, check_copy):
    from types import SimpleNamespace
    root=tmp_path/'snapshot';build=tmp_path/'build';build.mkdir()
    (build/'ask.py').write_text('ORIGINAL = True\n')
    note=tmp_path/'note.md';note.write_text('reviewed')
    excluded=str(tmp_path/'profile'/'excluded.md')
    entries={str(note):{'pass':True},excluded:{'pass':True,'description':'private metadata'}}
    def loader(path, name):
        if check_copy:
            assert path == root/'baseline'/'ask.py'
            (build/'ask.py').write_text('CHANGED = True\n')
            assert path.read_text()=='ORIGINAL = True\n'
        return SimpleNamespace(prepare_bulk=SimpleNamespace(CEILING_BYTES=1000),
            memory=lambda req:{'pointers':['p']},load_cache_files=lambda ptr:entries,
            connector_names=lambda ptr:[],refresh_would_admit=lambda path,ptr:True,
            has_secret=lambda text:False)
    monkeypatch.setattr(sr.scorecard,'load_ask',loader)
    target={'question':'target','principal':'reader','gold':[str(note)]}
    controls=[{'question':'control','principal':'reader','gold':['/control.md']}]
    pin=sr.freeze(target,controls,None,None,build/'ask.py',tmp_path/'cache',root)
    data=sr.validate_bundle(root,pin)
    assert excluded not in data['entries']['p']
    assert 'private metadata' not in (root/'manifest.json').read_text()


def test_caught_bypass_still_rejects(tmp_path):
    root,pin,note=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir()
    code=(root/'baseline'/'ask.py').read_text().replace("    text=Path(%r).read_bytes()" % str(note),
        "    try:\n        text=Path(%r).read_text().encode()\n    except PermissionError:\n        text=b'original'" % str(note))
    (candidate/'ask.py').write_text(code)
    result=sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')
    assert 'unfrozen file access attempted' in result['reasons']


def test_reserved_manifest_passed_to_scorecard(tmp_path):
    root,pin,_=bundle(tmp_path)
    data=sr.validate_bundle(root,pin)
    held={'question':'reserved','gold':['/reserved.md'],'principal':'reader','split':'held-out',
          'group':'reserved','source_family':'reserved'}
    sr.write_json(root/'held-out.json',{'version':1,'cases':[held]})
    data['reserved']=[held];data['cases'].append(held)
    data['held_out_sha256']=sr.digest((root/'held-out.json').read_bytes())
    sr.write_json(root/'manifest.json',data)
    pin=sr.digest((root/'manifest.json').read_bytes())
    sr.compare(root,pin,root/'baseline'/'ask.py',tmp_path/'result')
    report=json.loads((tmp_path/'result'/'scorecard.json').read_text())
    assert report['held_out_verified'] is True
    assert report['held_out_sha256']==data['held_out_sha256']



def test_child_process_cannot_read_live_note_through_pipe(tmp_path):
    root,pin,note=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir()
    code=(root/'baseline'/'ask.py').read_text().replace("    text=Path(%r).read_bytes()" % str(note),
        """    import os, subprocess
    reader, writer = os.pipe()
    try:
        child = subprocess.Popen(['cat', %r], stdout=writer)
        child.wait()
        text = os.read(reader, 100)
    finally:
        os.close(reader)
        os.close(writer)""" % str(note))
    (candidate/'ask.py').write_text(code)
    note.write_text('changed')
    with pytest.raises(PermissionError,match='subprocess.Popen'):
        sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')


@pytest.mark.parametrize('event', ['os.system', 'os.posix_spawn', 'os.exec', 'os.fork', 'os.forkpty'])
def test_process_audit_events_refused(event):
    violations=[]
    with sr.guarded_reads([],violations), pytest.raises(PermissionError,match='operation forbidden'):
        sys.audit(event)  # exercise the hook without starting/replacing the test process
    assert violations == ['forbidden operation: ' + event]


@pytest.mark.parametrize('operation', ['mkdir', 'rename', 'remove', 'symlink'])
def test_filesystem_mutations_refused(tmp_path, operation):
    import os
    source=tmp_path/'source';source.write_text('original')
    dest=tmp_path/'destination'
    calls={'mkdir':lambda:os.mkdir(dest),'rename':lambda:os.rename(source,dest),
           'remove':lambda:os.remove(source),'symlink':lambda:os.symlink(source,dest)}
    violations=[]
    with sr.guarded_reads([],violations), pytest.raises(PermissionError,match='operation forbidden'):
        calls[operation]()
    assert source.read_text()=='original' and not dest.exists()
    assert violations


def test_caught_filesystem_mutation_still_rejects(tmp_path):
    root,pin,note=bundle(tmp_path)
    candidate=tmp_path/'candidate';candidate.mkdir()
    dest=tmp_path/'unwanted-directory'
    code=(root/'baseline'/'ask.py').read_text().replace('    text=',
        "    try:\n        __import__('os').mkdir(%r)\n    except PermissionError:\n        pass\n    text=" % str(dest))
    (candidate/'ask.py').write_text(code)
    result=sr.compare(root,pin,candidate/'ask.py',tmp_path/'result')
    assert result['decision']=='REJECT'
    assert any('os.mkdir' in u for u in result['uncertainty'])
    assert not dest.exists()
