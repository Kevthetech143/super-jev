"""A re-register that changes nothing keeps the pointer's generation; any real change mints a new one."""
import json
import tempfile
import unittest
from pathlib import Path

from path_connect import connect
from service import Service


class GenerationKeepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'record.md'
        self.source.write_text('# Record\nFirst fact.\n\n## Detail\nSecond fact.\n')
        self.config = {'db': str(self.root / 'answers.sqlite'), 'registry': str(self.root / 'registry.json')}
        self.request = {'pointer': 'records', 'principals': ['owner'],
                        'sources': [{'path': str(self.source), 'description': 'A record [kind: note; status: done; as_of: unknown; subject: record]'}]}

    def reviewed(self, request=None):
        request = request or self.request
        preview = connect(request, self.config)
        return {**request, 'reviewed': True, 'replace': True,
                'sources': [{**s, 'sha256': p['sha256']} for s, p in zip(request['sources'], preview['sources'])]}

    def service(self):
        return Service(self.config['db'], self.config['registry'], lambda *_: {'status': 'no-match'})

    def generation(self):
        return self.service().pointer('records', 'owner')[0]['generation']

    def connect(self, request=None):
        result = connect(self.reviewed(request), self.config)
        self.assertEqual(result['status'], 'registered', result)
        return result

    def test_identical_reconnect_keeps_generation_and_prepared_folder(self):
        self.connect()
        before, folders = self.generation(), sorted(self.root.glob('.prepared-*'))
        self.connect()
        self.assertEqual(self.generation(), before)
        self.assertEqual(sorted(self.root.glob('.prepared-*')), folders)

    def test_identical_register_keeps_generation(self):
        self.connect()
        before = self.generation()
        self.service().register('records', 'records', ['owner'])
        self.assertEqual(self.generation(), before)

    def test_changed_file_gets_a_new_generation(self):
        self.connect()
        before = self.generation()
        self.source.write_text('# Record\nA changed fact.\n')
        self.connect()
        self.assertNotEqual(self.generation(), before)

    def test_changed_labels_get_a_new_generation(self):
        self.connect()
        before = self.generation()
        self.request['sources'][0]['description'] = 'A record [kind: note; status: open; as_of: 2026-01-02; subject: record]'
        self.connect()
        self.assertNotEqual(self.generation(), before)

    def test_changed_navigation_gets_a_new_generation(self):
        self.connect()
        before = self.generation()
        request = {**self.request, 'structure': 'folder-tree',
                   'sources': [{**self.request['sources'][0], 'navigationPath': ['Group']}]}
        preview = connect(request, self.config)
        result = connect({**self.reviewed(request), 'navigationSHA': preview['navigationSHA']}, self.config)
        self.assertEqual(result['status'], 'registered', result)
        self.assertNotEqual(self.generation(), before)

    def test_changed_principals_get_a_new_generation(self):
        self.connect()
        before = self.generation()
        self.service().register('records', 'records', ['owner', 'helper'])
        self.assertNotEqual(self.generation(), before)

    def test_identical_reconnect_leaves_the_registry_entry_unchanged(self):
        self.connect()
        entry = json.loads(Path(self.config['registry']).read_text())['datasets']['records']
        self.connect()
        again = json.loads(Path(self.config['registry']).read_text())['datasets']['records']
        self.assertEqual(again, entry)
        self.assertTrue(Path(entry['manifestPath']).is_file())
        self.assertIsNone(self.service().pointer('records', 'owner')[1])


if __name__ == '__main__':
    unittest.main()
