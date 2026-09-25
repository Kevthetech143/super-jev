"""End-to-end local onboarding contracts, using the production chunker."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from path_connect import connect
from service import Service


class PathConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'record.md'
        self.source.write_text('# Record\nFirst fact.\n\n## Detail\nSecond fact.\n')
        self.config = {'db': str(self.root / 'answers.sqlite'), 'registry': str(self.root / 'registry.json')}
        self.request = {'pointer': 'records', 'principals': ['owner'], 'sources': [{'path': str(self.source)}]}

    def reviewed(self):
        preview = connect(self.request, self.config)
        return {**self.request, 'reviewed': True, 'sources': preview['sources']}

    def service(self):
        return Service(self.config['db'], self.config['registry'], lambda *_: {'status': 'no-match'})

    def test_preview_has_no_mutation_or_provider_call(self):
        self.request['sources'][0]['description'] = 'Reviewed specific record description'
        before = sorted(p.name for p in self.root.iterdir())
        result = connect(self.request, self.config)
        self.assertEqual(result['reason'], 'review-required')
        self.assertEqual(result['sources'][0]['description'], self.request['sources'][0]['description'])
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(before, sorted(p.name for p in self.root.iterdir()))
        self.assertNotIn('First fact', json.dumps(result))

    def test_real_chunking_manifest_and_source_guards(self):
        result = connect(self.reviewed(), self.config)
        self.assertEqual(result['status'], 'registered', result)
        snapshot = self.service().snapshot('records')
        entry = snapshot['entry']
        self.assertNotIn('checkedAt', entry)
        manifest = json.loads(Path(entry['manifestPath']).read_text())
        self.assertEqual(len(manifest['preparations']), 2)
        self.assertEqual(manifest['sources'][0]['originalPath'], str(self.source.resolve()))
        self.assertEqual(result['sources'][0]['originalPath'], str(self.source.resolve()))
        lines = self.source.read_text().split('\n')
        for p in manifest['preparations']:
            self.assertEqual(p['reviewedText'], '\n'.join(lines[p['startLine'] - 1:p['endLine']]))
        self.assertEqual(Path(entry['manifestPath']).stat().st_mode & 0o777, 0o600)
        self.assertIsNone(self.service().pointer('records', 'owner')[1])
        self.source.write_text('Changed source')
        self.assertEqual(self.service().pointer('records', 'owner')[1]['status'], 'preparation-required')

    def test_all_or_nothing_unsupported(self):
        binary = self.root / 'bad.dat'
        binary.write_bytes(b'\x00\xff')
        self.request['sources'].append({'path': str(binary)})
        result = connect(self.request, self.config)
        self.assertEqual(result['reason'], 'unsupported-source')
        self.assertFalse(Path(self.config['registry']).exists())
        self.assertFalse(Path(self.config['db']).exists())
        self.assertFalse(list(self.root.glob('.prepared-*')))

    def test_hash_change_requires_new_review(self):
        request = self.reviewed()
        self.source.write_text('new text')
        result = connect(request, self.config)
        self.assertEqual(result['reason'], 'review-required')
        self.assertFalse(Path(self.config['db']).exists())

    def test_replace_preserves_scope_invalidates_cache(self):
        request = self.reviewed()
        self.assertEqual(connect(request, self.config)['status'], 'registered')
        service = self.service()
        pointer, _ = service.pointer('records', 'owner')
        with service.connect() as db:
            db.execute('INSERT INTO cache VALUES (?,?,?,?,?)', ('key', 'records', pointer['generation'], pointer['fingerprint'], '{}'))
            db.execute('INSERT INTO pending VALUES (?,?,?,?,?)', ('ticket', 'records', pointer['generation'], pointer['fingerprint'], '{}'))
        self.assertEqual(connect(request, self.config)['reason'], 'already-connected')
        self.assertEqual(connect({**request, 'replace': True, 'principals': ['other']}, self.config)['reason'], 'scope-change')
        self.assertNotIn('registeredPrincipals', connect({**request, 'replace': True, 'principals': ['other']}, self.config))
        self.assertEqual(connect({**request, 'replace': True}, self.config)['status'], 'registered')
        fresh, _ = service.pointer('records', 'owner')
        self.assertNotEqual(pointer['generation'], fresh['generation'])
        with service.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cache').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pending').fetchone()[0], 0)

    def test_narrowed_refresh_names_the_registered_scope(self):
        wide = {**self.reviewed(), 'principals': ['owner', 'helper']}
        self.assertEqual(connect(wide, self.config)['status'], 'registered')
        narrowed = connect({**wide, 'replace': True, 'principals': ['owner']}, self.config)
        self.assertEqual((narrowed['reason'], narrowed['registeredPrincipals']), ('scope-change', ['helper', 'owner']))
        empty = connect({**wide, 'replace': True, 'principals': []}, self.config)
        self.assertNotIn('registeredPrincipals', empty)

    def test_partial_publication_fails_closed(self):
        request = self.reviewed()
        self.assertEqual(connect(request, self.config)['status'], 'registered')
        with patch.object(Service, 'register', side_effect=ValueError('secret private text')):
            result = connect({**request, 'replace': True}, self.config)
        self.assertEqual(result['reason'], 'connect-failed')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.service().pointer('records', 'owner')[1]['status'], 'preparation-required')
        self.assertEqual(connect({**request, 'replace': True}, self.config)['status'], 'registered')

    def test_first_registration_interruption_recoverable_with_same_scope(self):
        request = self.reviewed()
        with patch.object(Service, 'register', side_effect=ValueError('interrupted')):
            self.assertEqual(connect(request, self.config)['reason'], 'connect-failed')
        self.assertEqual(connect({**request, 'replace': True, 'principals': ['other']}, self.config)['reason'], 'unowned-dataset')
        self.assertEqual(connect({**request, 'replace': True}, self.config)['status'], 'registered')

    def test_duplicate_directory_and_limits(self):
        duplicate = {**self.request, 'sources': self.request['sources'] * 2}
        self.assertEqual(connect(duplicate, self.config)['reason'], 'duplicate-source')
        directory = {**self.request, 'sources': [{'path': str(self.root)}]}
        self.assertEqual(connect(directory, self.config)['reason'], 'unsupported-source')
        self.assertEqual(connect({**self.request, 'sources': self.request['sources'] * 51}, self.config)['reason'], 'source-limit')
        self.assertFalse(Path(self.config['db']).exists())


if __name__ == '__main__':
    unittest.main()
