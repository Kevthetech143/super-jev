"""Split integrity and frozen reservation regressions; entirely offline."""
import hashlib
import json
from pathlib import Path
import pytest
from test_scorecard import sc, _w, _build


def case(q='held', split='held-out', gold='/held.md', **kw):
    return dict(question=q, gold=[gold], principal='me', split=split,
                group=q, source_family=q, **kw)


def test_split_reports_and_held_loss_despite_tuned_gain(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SC_STATE', str(tmp_path / 'state'))
    f = tmp_path / 'cases.jsonl'
    _w(f, [case(), case('tuned', 'tuned', '/tuned.md'),
           {'question': 'past', 'gold': ['/past.md'], 'principal': 'me'}])
    old = _build(tmp_path, 'old', ['/held.md'])
    new = _build(tmp_path, 'new', ['/tuned.md'])
    assert sc.main(['--principal', 'me', '--no-harvest', '--cases', str(f),
                    '--ask', old, '--ask', new, '--json']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['splits']['held-out']['lost'] == 1
    assert report['splits']['tuned']['gained'] == 1
    assert report['splits']['retrospective']['cases'] == 1
    assert report['held_out_verified'] is False


@pytest.mark.parametrize('field,value', [('group', 'same'), ('source_family', 'same'),
                                          ('gold', ['/same.md']), ('question', 'Same?')])
def test_overlap_is_rejected(field, value):
    a, b = case(), case('other', 'tuned', '/other.md')
    a[field] = b[field] = value
    with pytest.raises(ValueError, match='cross-split'):
        sc.normalize_cases([a, b], 'me')


def test_harvested_and_unknown_legacy_splits_are_retrospective():
    assert sc.normalize_cases([case(split='harvested')], 'me')[0]['split'] == 'retrospective'
    row = sc.normalize_cases([case(split="older-label")], "me")[0]
    assert row["split"] == "retrospective"
    assert row["original_split"] == "older-label"


def test_freeze_is_exclusive_and_checksum_checked_before_build(tmp_path, capsys):
    f, frozen = tmp_path / 'cases.jsonl', tmp_path / 'reserved.json'
    _w(f, [case()])
    args = ['--principal', 'me', '--cases', str(f), '--freeze-held-out', str(frozen)]
    assert sc.main(args) == 0
    digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert digest in capsys.readouterr().out
    assert sc.frozen_cases(frozen, digest)[0]['question'] == 'held'
    with pytest.raises(SystemExit) as error:
        sc.main(args)
    assert error.value.code == 2
    frozen.write_bytes(frozen.read_bytes() + b' ')
    with pytest.raises(SystemExit) as error:
        sc.main(['--principal', 'me', '--held-out', str(frozen), '--held-out-sha256', digest,
                 '--ask', '/does-not-exist.py'])
    assert error.value.code == 2
    assert 'checksum mismatch' in capsys.readouterr().err


def test_verified_reservation_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SC_STATE', str(tmp_path / 'state'))
    f, frozen = tmp_path / 'cases.jsonl', tmp_path / 'reserved.json'
    _w(f, [case()])
    sc.main(['--principal', 'me', '--cases', str(f), '--freeze-held-out', str(frozen)])
    capsys.readouterr()
    digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
    build = _build(tmp_path, 'build', ['/held.md'])
    assert sc.main(['--principal', 'me', '--no-harvest', '--held-out', str(frozen),
                    '--held-out-sha256', digest, '--ask', build, '--json']) == 0
    assert json.loads(capsys.readouterr().out)['held_out_verified'] is True


def test_missing_family_cannot_be_reserved(tmp_path):
    row = case(); row.pop('group')
    f = tmp_path / 'cases.jsonl'; _w(f, [row])
    with pytest.raises(SystemExit) as e:
        sc.main(['--principal', 'me', '--cases', str(f), '--freeze-held-out', str(tmp_path / 'frozen')])
    assert e.value.code == 2


def test_frozen_case_cannot_gain_an_extra_gold_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SC_STATE', str(tmp_path / 'state'))
    f, frozen = tmp_path / 'cases.jsonl', tmp_path / 'reserved.json'
    _w(f, [case()])
    sc.main(['--principal', 'me', '--cases', str(f), '--freeze-held-out', str(frozen)])
    digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
    row = case(); row['gold'].append('/extra.md'); _w(f, [row])
    build = _build(tmp_path, 'build', ['/extra.md'])
    with pytest.raises(SystemExit) as e:
        sc.main(['--principal', 'me', '--no-harvest', '--held-out', str(frozen),
                 '--held-out-sha256', digest, '--cases', str(f), '--ask', build])
    assert e.value.code == 2
    assert 'differs from the frozen' in capsys.readouterr().err


def test_malformed_explicit_case_does_not_disappear(tmp_path):
    f = tmp_path / 'cases.jsonl'
    f.write_text(json.dumps(case()) + '\nnot-json\n')
    with pytest.raises(SystemExit) as e:
        sc.main(['--principal', 'me', '--cases', str(f), '--freeze-held-out', str(tmp_path / 'frozen')])
    assert e.value.code == 2
    assert not (tmp_path / 'frozen').exists()


def test_loss_in_any_compared_build_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SC_STATE', str(tmp_path / 'state'))
    f = tmp_path / 'cases.jsonl'; _w(f, [case()])
    old = _build(tmp_path, 'old', ['/held.md'])
    bad = _build(tmp_path, 'bad', ['/other.md'])
    good = _build(tmp_path, 'good', ['/held.md'])
    assert sc.main(['--principal', 'me', '--no-harvest', '--cases', str(f),
                    '--ask', old, '--ask', bad, '--ask', good, '--json']) == 1
    assert json.loads(capsys.readouterr().out)['splits']['held-out']['lost'] == 1


def test_duplicate_question_cannot_discard_family_metadata():
    first, second = case(), case()
    second['group'] = 'other-group'
    with pytest.raises(ValueError, match='conflicting family'):
        sc.normalize_cases([first, second], 'me')


def test_empty_gold_is_reported_as_excluded_not_passed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('SC_STATE', str(tmp_path / 'state'))
    f = tmp_path / 'cases.jsonl'
    _w(f, [{'question': 'unknown', 'gold': [], 'principal': 'me'}, case()])
    build = _build(tmp_path, 'build', ['/held.md'])
    assert sc.main(['--principal', 'me', '--no-harvest', '--cases', str(f), '--ask', build, '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['cases'] == 1 and report['excluded_no_gold'] == 1
