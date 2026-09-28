"""A stale pointer routed on its last registration (navigate lastGood), not refused.

A refresh cooldown left a set stale for minutes; every ask refused the whole set, so its
unchanged notes were invisible too. lastGood routes on the catalog reviewed at connect.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cli import load_config, run
from path_connect import connect
from service import Service


def pick_all(question, catalog, limits):
    """A provider that returns every leaf, and remembers the catalog it was shown."""
    pick_all.seen.append(catalog)
    root = next(n for n in catalog['nodes'] if n['id'] == catalog['rootId'])
    leaves = [n for n in catalog['nodes'] if 'sourceId' in n]
    def path_to(node_id):
        for child in root['children']:
            if child == node_id:
                return [root['id'], node_id]
            node = next(n for n in catalog['nodes'] if n['id'] == child)
            if node_id in node.get('children', []):
                return [root['id'], child, node_id]
        raise AssertionError(node_id)
    return {'status': 'candidates', 'calls': 1, 'trace': [], 'complete': False, 'message': 'ok',
            'candidates': [{'sourceId': n['sourceId'], 'nodeId': n['id'], 'path': path_to(n['id']),
                            'score': 0.9} for n in leaves]}


class LastGoodTests(unittest.TestCase):
    def setUp(self):
        pick_all.seen = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.one, self.two = self.root / 'one.md', self.root / 'two.md'
        self.one.write_text('We tried the cache warmer; it did not help.\n')
        self.two.write_text('Release traps.\n')
        self.config = {'db': str(self.root / 'db.sqlite'), 'registry': str(self.root / 'registry.json')}
        request = {'pointer': 'notes', 'principals': ['owner'],
                   'sources': [{'path': str(self.one), 'description': 'Tried before'},
                               {'path': str(self.two), 'description': 'Traps'}]}
        preview = connect(request, self.config)
        self.assertEqual(connect({**request, 'reviewed': True, 'navigationSHA': preview['navigationSHA'],
                                  'sources': preview['sources']}, self.config)['status'], 'registered')
        self.service = Service(self.config['db'], self.config['registry'], lambda *_: {},
                               navigate_provider=pick_all)

    def paths(self, result):
        return sorted(c['originalPath'] for c in result['candidates'])

    def test_a_stale_pointer_is_routed_on_its_last_registration(self):
        self.two.write_text('Release traps.\nA note written minutes ago.\n')
        # The default contract is unchanged: a stale pointer is refused.
        self.assertEqual(self.service.navigate('notes', 'owner', 'tried')['status'], 'preparation-required')
        self.assertEqual(pick_all.seen, [])
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(result['status'], 'candidates')
        self.assertEqual(self.paths(result), sorted([os.path.abspath(self.one), os.path.abspath(self.two)]))
        self.assertEqual(result['stale'], {'status': 'preparation-required',
                                           'changed': [os.path.abspath(self.two)], 'missing': []})
        # Only the descriptions reviewed at connect reach the provider.
        self.assertEqual(sorted(n.get('description') for n in pick_all.seen[0]['nodes'] if 'sourceId' in n),
                         ['Traps', 'Tried before'])

    def test_a_fresh_pointer_has_no_stale_mark(self):
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(result['status'], 'candidates')
        self.assertNotIn('stale', result)

    def test_a_deleted_file_is_never_a_candidate(self):
        self.two.unlink()
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(self.paths(result), [os.path.abspath(self.one)])
        self.assertEqual(result['stale']['missing'], [os.path.abspath(self.two)])

    def test_only_deleted_files_matched_is_no_candidates(self):
        self.one.unlink()
        self.two.unlink()
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual((result['status'], result['candidates']), ('no-candidates', []))

    def test_another_principal_is_still_denied(self):
        self.two.write_text('changed\n')
        self.assertEqual(self.service.navigate('notes', 'intruder', 'tried', last_good=True)['status'],
                         'access-denied')
        self.assertEqual(pick_all.seen, [])

    def test_a_reprepared_manifest_falls_back_to_the_registered_sources(self):
        registry = json.loads(Path(self.config['registry']).read_text())
        Path(registry['datasets']['notes']['manifestPath']).write_text('{}')
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(len(result['candidates']), 2)
        self.assertIn('stale', result)

    def test_re_registration_during_navigation_discards_candidates(self):
        self.two.write_text('changed\n')
        def provider(q, catalog, limits):
            out = pick_all(q, catalog, limits)
            self.two.write_text('Release traps.\n')
            self.service.register('notes', 'notes', ['owner'])  # a refresh finished meanwhile
            return out
        self.service.navigate_provider = provider
        self.assertEqual(self.service.navigate('notes', 'owner', 'tried', last_good=True)['status'],
                         'pointer-changed')

    def test_a_file_changed_or_deleted_during_navigation_is_reported(self):
        self.two.write_text('changed\n')
        def provider(q, catalog, limits):
            out = pick_all(q, catalog, limits)
            self.one.write_text('edited while Jev routed\n')
            return out
        self.service.navigate_provider = provider
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(result['stale']['changed'], sorted([os.path.abspath(self.one), os.path.abspath(self.two)]))
        def deleting(q, catalog, limits):
            out = pick_all(q, catalog, limits)
            self.one.unlink()
            return out
        self.service.navigate_provider = deleting
        result = self.service.navigate('notes', 'owner', 'tried', last_good=True)
        self.assertEqual(self.paths(result), [os.path.abspath(self.two)])
        self.assertEqual(result['stale']['missing'], [os.path.abspath(self.one)])

    def test_navigate_many_and_the_cli_pass_last_good(self):
        self.two.write_text('changed\n')
        many = self.service.navigate_many(['notes'], 'owner', 'tried', last_good=True)
        self.assertIn('stale', many['results']['notes'])
        fake = self.root / 'fake_navigation.py'
        fake.write_text(
            'import json,sys\n'
            'p=json.load(sys.stdin); leaf=next(n for n in p["catalog"]["nodes"] if "sourceId" in n)\n'
            'print(json.dumps({"status":"candidates","candidates":[{"sourceId":leaf["sourceId"],"nodeId":leaf["id"],'
            '"path":["root",leaf["id"]],"score":1}],"calls":1,"trace":[],"complete":False,"message":"ok"}))\n')
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps({**self.config, 'navigationCommand': [sys.executable, str(fake)]}))
        request = {'action': 'navigate', 'pointer': 'notes', 'principal': 'owner', 'question': 'tried'}
        self.assertEqual(run(request, load_config(config_path))['status'], 'preparation-required')
        result = run({**request, 'lastGood': True}, load_config(config_path))
        self.assertEqual(result['status'], 'candidates')
        self.assertIn('stale', result)
        with self.assertRaises(ValueError):
            run({**request, 'lastGood': 'yes'}, load_config(config_path))


if __name__ == '__main__':
    unittest.main()
