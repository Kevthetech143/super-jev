"""The watched-folder rule lives in the engine: a file whose real path lies under a watched pointer's folders may
only be registered to a pointer whose agents are a subset of the watched pointer's agents. Every path in (connect,
register, a second pointer on the same dataset) meets it. Names are made up."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).with_name('cli.py')


class WatchedRuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'db': str(self.root / 'db.sqlite'), 'registry': str(self.root / 'registry.json')}))
        self.garden = self.root / 'garden'
        (self.garden / 'beds').mkdir(parents=True)
        (self.garden / 'beds' / 'kale.md').write_text('# Kale\nQuill sows kale in March.\n')
        (self.garden / 'shed.md').write_text('# Shed\nThe shed key hangs by the door.\n')
        (self.garden / 'tools.md').write_text('# Tools\nThe spade lives in the shed.\n')

    def tearDown(self):
        self._tmp.cleanup()

    def call(self, body):
        request = self.root / 'request.json'
        request.write_text(json.dumps(body))
        proc = subprocess.run([sys.executable, str(CLI), '--config', str(self.config), '--input', str(request)],
                              capture_output=True, text=True)
        return json.loads(proc.stdout)

    def connect(self, pointer, principals, files, replace=False):
        body = {'action': 'connect', 'pointer': pointer, 'principals': principals,
                'sources': [{'path': str(f)} for f in files], 'replace': replace}
        preview = self.call(body)
        if preview.get('reason') != 'review-required':
            return preview
        return self.call({**body, 'sources': preview['sources'], 'reviewed': True})

    def watch(self, pointer, principals, folder):
        return self.call({'action': 'watch', 'pointer': pointer, 'principals': principals, 'folders': [str(folder)]})

    def pointers(self, principal):
        return [p['pointer'] for p in self.call({'action': 'panel', 'principal': principal})['pointers']]

    def registry_datasets(self):
        path = self.root / 'registry.json'
        return sorted(json.loads(path.read_text())['datasets']) if path.exists() else []

    def watched_quill(self):
        self.assertEqual(self.connect('quill-garden', ['quill'], [self.garden / 'shed.md'])['status'], 'registered')
        self.assertEqual(self.watch('quill-garden', ['quill'], self.garden)['status'], 'ok')

    # N1: the engine's own connect path (dispatch.py memory --input) used to skip the helper check.
    def test_engine_connect_of_other_agent_file_under_watched_folder_is_refused(self):
        self.watched_quill()
        refused = self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])
        self.assertEqual(refused['status'], 'error')
        self.assertEqual(refused['reason'], 'watched-refused')
        self.assertIn('quill-garden', refused['message'])
        self.assertIn('--unwatch --pointer quill-garden', refused['message'])
        self.assertNotIn('quill', refused['message'].replace('quill-garden', ''))  # no agent names leak
        self.assertEqual(refused['files'], [str(self.garden / 'beds' / 'kale.md')])
        self.assertEqual(self.pointers('rook'), [])
        self.assertNotIn('rook-beds', self.registry_datasets())

    def test_register_of_the_watched_dataset_for_another_agent_is_refused(self):
        self.watched_quill()
        refused = self.call({'action': 'register', 'pointer': 'rook-copy', 'dataset': 'quill-garden', 'principals': ['rook']})
        self.assertEqual(refused['reason'], 'watched-refused')
        self.assertEqual(self.pointers('rook'), [])
        ok = self.call({'action': 'register', 'pointer': 'quill-copy', 'dataset': 'quill-garden', 'principals': ['quill']})
        self.assertEqual(ok['status'], 'registered')

    def test_subset_of_the_watched_agents_is_allowed(self):
        self.assertEqual(self.connect('duo-garden', ['quill', 'rook'], [self.garden / 'shed.md'])['status'], 'registered')
        self.assertEqual(self.watch('duo-garden', ['quill', 'rook'], self.garden)['status'], 'ok')
        self.assertEqual(self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])['status'], 'registered')
        self.assertEqual(self.connect('duo-more', ['rook', 'quill'], [self.garden / 'tools.md'])['status'], 'registered')

    # N2: marking a folder must not freeze an enclosing pointer of other agents; only the watched files are refused.
    def test_enclosing_pointer_keeps_refreshing_and_only_watched_files_are_refused(self):
        outside = self.root / 'notes.md'
        outside.write_text('# Notes\nRook keeps the seed list here.\n')
        self.watched_quill()
        self.assertEqual(self.connect('rook-all', ['rook'], [outside])['status'], 'registered')
        refused = self.connect('rook-all', ['rook'], [outside, self.garden / 'beds' / 'kale.md'], replace=True)
        self.assertEqual(refused['reason'], 'watched-refused')
        self.assertEqual(refused['files'], [str(self.garden / 'beds' / 'kale.md')])  # only the watched file is named
        outside.write_text('# Notes\nRook keeps the seed list and the bulb list here.\n')
        again = self.connect('rook-all', ['rook'], [outside], replace=True)  # leaving it out, the pointer refreshes
        self.assertEqual(again['status'], 'registered')
        check = self.call({'action': 'watched-check', 'pointer': 'rook-all', 'principals': ['rook'],
                           'paths': [str(outside), str(self.garden / 'beds' / 'kale.md')]})
        self.assertEqual([r['path'] for r in check['refused']], [str(self.garden / 'beds' / 'kale.md')])

    def test_mark_refused_when_another_pointer_holds_a_file_inside_for_other_agents(self):
        self.assertEqual(self.connect('quill-garden', ['quill'], [self.garden / 'shed.md'])['status'], 'registered')
        self.assertEqual(self.connect('rook-tools', ['rook'], [self.garden / 'tools.md'])['status'], 'registered')
        refused = self.watch('quill-garden', ['quill'], self.garden)
        self.assertEqual(refused['status'], 'error')
        self.assertIn('rook-tools', refused['message'])
        # a pointer whose agents are a subset does not block the mark
        self.assertEqual(self.connect('quill-tools', ['quill'], [self.garden / 'beds' / 'kale.md'])['status'], 'registered')
        self.call({'action': 'remove', 'pointer': 'rook-tools'})
        self.assertEqual(self.watch('quill-garden', ['quill'], self.garden)['status'], 'ok')

    def test_mark_survives_a_refresh_and_unwatch_clears_it(self):
        self.watched_quill()
        self.assertEqual(self.connect('quill-garden', ['quill'], [self.garden / 'shed.md'], replace=True)['status'], 'registered')
        self.assertEqual(self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])['reason'], 'watched-refused')
        self.assertEqual(self.call({'action': 'unwatch', 'pointer': 'quill-garden', 'principals': ['quill']})['status'], 'ok')
        self.assertEqual(self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])['status'], 'registered')

    def test_watched_pointer_cannot_move_away_from_a_pointer_inside_it(self):
        self.assertEqual(self.connect('duo-garden', ['quill', 'rook'], [self.garden / 'shed.md'])['status'], 'registered')
        self.assertEqual(self.watch('duo-garden', ['quill', 'rook'], self.garden)['status'], 'ok')
        self.assertEqual(self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])['status'], 'registered')
        refused = self.call({'action': 'register', 'pointer': 'duo-garden', 'dataset': 'duo-garden', 'principals': ['quill']})
        self.assertEqual(refused['reason'], 'watched-refused')
        self.assertIn('rook-beds', refused['message'])

    def test_real_path_decides_a_link_into_a_watched_folder(self):
        self.watched_quill()
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        (elsewhere / 'link.md').symlink_to(self.garden / 'beds' / 'kale.md')
        refused = self.connect('rook-link', ['rook'], [elsewhere / 'link.md'])
        self.assertEqual(refused['reason'], 'watched-refused')

    # r5: "inside" is folder identity, not spelling.
    def watched_cafe(self):
        import unicodedata
        cafe = self.root / unicodedata.normalize('NFC', 'caf\u00e9')
        cafe.mkdir()
        (cafe / 'menu.md').write_text('# Menu\nQuill lists the teas.\n')
        self.assertEqual(self.connect('quill-cafe', ['quill'], [cafe / 'menu.md'])['status'], 'registered')
        self.assertEqual(self.watch('quill-cafe', ['quill'], cafe)['status'], 'ok')
        return cafe

    def test_unicode_form_of_the_path_does_not_escape_the_mark(self):
        import unicodedata
        cafe = self.watched_cafe()
        (cafe / 'new.md').write_text('# New\nRook note.\n')
        nfd = str(cafe).replace(unicodedata.normalize('NFC', 'caf\u00e9'), unicodedata.normalize('NFD', 'caf\u00e9'))
        if not (Path(nfd) / 'new.md').exists():
            self.skipTest('file system does not treat the NFD spelling as the same folder')
        refused = self.connect('rook-cafe', ['rook'], [Path(nfd) / 'new.md'])
        self.assertEqual(refused.get('reason'), 'watched-refused', refused)

    def test_firmlink_spelling_does_not_escape_the_mark(self):
        firm = Path('/System/Volumes/Data') / str(self.garden).lstrip('/')
        if not firm.exists():
            self.skipTest('no firmlink spelling on this system')
        self.watched_quill()
        refused = self.connect('rook-firm', ['rook'], [firm / 'beds' / 'kale.md'])
        self.assertEqual(refused.get('reason'), 'watched-refused', refused)

    def test_case_spelling_does_not_escape_the_mark(self):
        if not (self.root / 'GARDEN').exists():
            self.skipTest('case-sensitive file system')
        self.watched_quill()
        refused = self.connect('rook-case', ['rook'], [self.root / 'GARDEN' / 'beds' / 'kale.md'])
        self.assertEqual(refused.get('reason'), 'watched-refused', refused)

    def test_watched_pointer_agents_may_only_shrink(self):
        self.watched_quill()
        for agents in (['quill', 'rook'], ['rook']):
            refused = self.call({'action': 'register', 'pointer': 'quill-garden', 'dataset': 'quill-garden', 'principals': agents})
            self.assertEqual(refused.get('reason'), 'watched-refused', refused)
            self.assertIn('--unwatch --pointer quill-garden', refused['message'])
        self.assertEqual(self.pointers('rook'), [])

    def test_mark_needs_a_folder_that_holds_the_pointers_own_file(self):
        self.assertEqual(self.connect('quill-garden', ['quill'], [self.garden / 'shed.md'])['status'], 'registered')
        other = self.root / 'elsewhere'
        other.mkdir()
        self.assertEqual(self.watch('quill-garden', ['quill'], other)['status'], 'error')
        self.assertEqual(self.watch('quill-garden', ['quill'], self.root)['status'], 'ok')  # an enclosing folder holds it

    def test_refusal_at_register_leaves_no_prepared_copy_and_restores_the_registry(self):
        import json as _json, unittest.mock as mock
        import path_connect
        from service import Service, WatchedRefused
        self.assertEqual(self.connect('rook-beds', ['rook'], [self.garden / 'beds' / 'kale.md'])['status'], 'registered')
        before = (self.root / 'registry.json').read_text()
        config = _json.loads(self.config.read_text())
        prepared = lambda: sorted(p.name for p in self.root.glob('.prepared-*'))
        kept = prepared()
        body = {'action': 'connect', 'pointer': 'rook-beds', 'principals': ['rook'], 'replace': True, 'reviewed': True,
                'sources': [{'path': str(self.garden / 'beds' / 'kale.md')}]}
        body['sources'] = path_connect.connect({**body, 'reviewed': False}, config)['sources']
        with mock.patch.object(Service, 'register', side_effect=WatchedRefused('cannot register: test')):
            got = path_connect.connect(body, config)
        self.assertEqual(got['reason'], 'watched-refused')
        self.assertEqual(prepared(), kept)  # no new .prepared-* folder
        self.assertEqual((self.root / 'registry.json').read_text(), before)


if __name__ == '__main__':
    unittest.main()
