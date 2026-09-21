"""Connector navigation metadata and authorization contracts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cli import load_config, run
from path_connect import connect
from service import Service


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'team').mkdir()
        self.one = self.root / 'team' / 'one.md'
        self.two = self.root / 'two.md'
        self.one.write_text('one\n')
        self.two.write_text('two\n')
        self.config = {'db': str(self.root / 'db.sqlite'),
                       'registry': str(self.root / 'registry.json')}
        self.request = {'pointer': 'records', 'principals': ['owner'],
                        'structure': 'folder-tree',
                        'sources': [{'path': str(self.one),
                                     'description': 'First',
                                     'navigationPath': ['People', 'Team']},
                                    {'path': str(self.two),
                                     'description': 'Second'}]}

    def confirm(self, preview, request=None):
        request = request or self.request
        return {**request, 'reviewed': True,
                'navigationSHA': preview['navigationSHA'],
                'sources': preview['sources']}

    def test_metadata_round_trip_and_changed_catalog_rejected(self):
        preview = connect(self.request, self.config)
        self.assertEqual(preview['catalog']['structure'], 'folder-tree')
        self.assertEqual(preview['catalog']['rootId'], 'root')
        self.assertLessEqual(len(preview['catalog']['nodes']), 200)
        changed = self.confirm(preview)
        changed['sources'] = [{**changed['sources'][0], 'description': 'Changed'},
                              changed['sources'][1]]
        self.assertEqual(connect(changed, self.config)['reason'], 'review-required')
        result = connect(self.confirm(preview), self.config)
        self.assertEqual(result['status'], 'registered')
        manifest = json.loads(Path(json.loads(Path(self.config['registry']).read_text())
                                   ['datasets']['records']['manifestPath']).read_text())
        self.assertEqual(manifest['catalog'], preview['catalog'])

    def test_unsupported_structure_and_legacy_flat_confirmation(self):
        bad = connect({**self.request, 'structure': 'filesystem'}, self.config)
        self.assertEqual(bad['reason'], 'unsupported-structure')
        legacy = {'pointer': 'records', 'principals': ['owner'],
                  'sources': [{'path': str(self.one)}]}
        preview = connect(legacy, self.config)
        result = connect({**legacy, 'reviewed': True,
                          'sources': preview['sources']}, self.config)
        self.assertEqual(result['status'], 'registered')

    def test_supplied_navigation_hash_is_always_enforced(self):
        legacy = {'pointer': 'records', 'principals': ['owner'],
                  'sources': [{'path': str(self.one)}]}
        preview = connect(legacy, self.config)
        result = connect({**legacy, 'reviewed': True,
                          'navigationSHA': 'wrong-hash',
                          'sources': preview['sources']}, self.config)
        self.assertEqual(result['reason'], 'review-required')
        self.assertFalse(Path(self.config['db']).exists())

    def test_grouping_bounds(self):
        too_deep = {**self.request, 'sources': [
            {**self.request['sources'][0], 'navigationPath': ['x'] * 9}]}
        self.assertEqual(connect(too_deep, self.config)['reason'],
                         'invalid-navigation-path')
        flat_grouped = {**self.request, 'structure': 'flat-files'}
        self.assertEqual(connect(flat_grouped, self.config)['reason'],
                         'invalid-navigation-path')

        deep = self.root.joinpath(*(["nested"] * 9))
        deep.mkdir(parents=True)
        deep_file = deep / 'deep.md'
        deep_file.write_text('deep\n')
        derived = {**self.request, 'sources': [{'path': str(deep_file)},
                                               {'path': str(self.two)}]}
        self.assertEqual(connect(derived, self.config)['reason'],
                         'invalid-navigation-path')

    def registered_service(self, provider):
        preview = connect(self.request, self.config)
        self.assertEqual(connect(self.confirm(preview), self.config)['status'],
                         'registered')
        return Service(self.config['db'], self.config['registry'], lambda *_: {},
                       navigate_provider=provider)

    def test_denied_and_stale_do_not_call_provider(self):
        calls = []
        service = self.registered_service(lambda *args: calls.append(args))
        self.assertEqual(service.navigate('records', 'intruder', 'where')['status'],
                         'access-denied')
        self.one.write_text('changed\n')
        self.assertEqual(service.navigate('records', 'owner', 'where')['status'],
                         'preparation-required')
        self.assertEqual(calls, [])

    def test_source_change_during_navigation_discards_candidates(self):
        def provider(*_):
            self.one.write_text('changed\n')
            return {'status': 'candidates', 'candidates': [], 'calls': 1,
                    'trace': [], 'complete': False, 'message': 'done'}
        result = self.registered_service(provider).navigate('records', 'owner', 'where')
        self.assertEqual(result['status'], 'preparation-required')
        self.assertNotIn('candidates', result)

    def test_unknown_provider_source_id_fails_closed(self):
        provider = lambda *_: {
            'status': 'candidates', 'candidates': [
                {'sourceId': 'unknown', 'nodeId': 'x', 'path': ['root', 'x'],
                 'score': 1}],
            'calls': 1, 'trace': [], 'complete': False, 'message': 'done'}
        result = self.registered_service(provider).navigate('records', 'owner', 'where')
        self.assertEqual(result, {'status': 'error',
                                  'reason': 'Navigation returned invalid output.'})

    def test_candidate_must_bind_catalog_path_score_and_unique_leaf(self):
        preview = connect(self.request, self.config)
        self.assertEqual(connect(self.confirm(preview), self.config)['status'],
                         'registered')
        root_node = next(node for node in preview['catalog']['nodes']
                         if node['id'] == 'root')
        leaf = next(node for node in preview['catalog']['nodes']
                    if 'sourceId' in node and node['id'] in root_node['children'])
        base = {'sourceId': leaf['sourceId'], 'nodeId': leaf['id'],
                'path': ['root', leaf['id']], 'score': 1}
        bad_candidates = [
            [{**base, 'nodeId': 'invented', 'path': ['root', 'invented']}],
            [{**base, 'path': [leaf['id']]}],
            [{**base, 'score': 1.1}],
            [base, base],
        ]
        for candidates in bad_candidates:
            provider = lambda *_, candidates=candidates: {
                'status': 'candidates', 'candidates': candidates, 'calls': 1,
                'trace': [], 'complete': False, 'message': 'done'}
            service = Service(self.config['db'], self.config['registry'],
                              lambda *_: {}, navigate_provider=provider)
            self.assertEqual(service.navigate('records', 'owner', 'where')['status'],
                             'error')

    def test_navigation_subprocess_integration(self):
        fake = self.root / 'fake_navigation.py'
        fake.write_text(
            'import json,sys\n'
            'p=json.load(sys.stdin); root=next(n for n in p["catalog"]["nodes"] if n["id"]=="root"); leaf=next(n for n in p["catalog"]["nodes"] if "sourceId" in n and n["id"] in root["children"])\n'
            'print(json.dumps({"status":"candidates","candidates":[{"sourceId":leaf["sourceId"],"nodeId":leaf["id"],"path":["root",leaf["id"]],"score":1}],"calls":1,"trace":[],"complete":False,"message":"ok"}))\n')
        preview = connect(self.request, self.config)
        connect(self.confirm(preview), self.config)
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps({**self.config,
                                           'navigationCommand': [sys.executable, str(fake)]}))
        result = run({'action': 'navigate', 'pointer': 'records',
                      'principal': 'owner', 'question': 'where'},
                     load_config(config_path))
        self.assertEqual(result['status'], 'candidates')
        self.assertEqual(result['candidates'][0]['originalPath'], str(self.two.resolve()))
        self.assertEqual(result['complete'], False)


if __name__ == '__main__':
    unittest.main()
