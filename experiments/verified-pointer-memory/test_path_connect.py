"""End-to-end local onboarding contracts, using the production chunker."""
import hashlib
import os
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
        # Recorded as given (absolute, symlinks kept); /var -> /private/var on macOS stays /var.
        self.assertEqual(manifest['sources'][0]['originalPath'], os.path.abspath(self.source))
        self.assertEqual(result['sources'][0]['originalPath'], os.path.abspath(self.source))
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

    def test_flat_leaves_sharing_a_file_name_carry_their_folder(self):
        from path_connect import _catalog
        sources = [{'id': str(i), 'path': f'/skills/{d}/{n}', 'description': 'd', 'navigationPath': None}
                   for i, (d, n) in enumerate([('ebay-return-label', 'SKILL.md'), ('buying-power', 'SKILL.md'),
                                               ('notes', 'plan.md')])]
        labels = sorted(n['label'] for n in _catalog(sources, 'flat-files')['nodes'] if 'sourceId' in n)
        self.assertEqual(labels, ['buying-power/SKILL.md', 'ebay-return-label/SKILL.md', 'plan.md'])


if __name__ == '__main__':
    unittest.main()


class LinkedSourceTests(unittest.TestCase):
    def test_a_source_under_a_moving_link_follows_the_link(self):
        """The skills catalog held Super Jev's own SKILL.md at a resolved release path
        (releases/v1.0.46/...): after each release the pointer kept serving the old copy and
        was never marked stale, because that old file never changed."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "v1").mkdir(); (d / "v2").mkdir()
            (d / "v1" / "SKILL.md").write_text("# Skill\nold release text\n")
            (d / "v2" / "SKILL.md").write_text("# Skill\nnew release text\n")
            link = d / "current"
            link.symlink_to(d / "v1")
            config = {"db": str(d / "a.sqlite"), "registry": str(d / "registry.json")}
            src = str(link / "SKILL.md")
            req = {"action": "connect", "pointer": "cat", "principals": ["owner"],
                   "sources": [{"path": src, "description": "skill"}]}
            prev = connect(req, config)
            req["sources"][0]["sha256"] = prev["sources"][0]["sha256"]
            self.assertEqual(connect({**req, "reviewed": True}, config)["status"], "registered")
            original = json.loads((d / "registry.json").read_text())["datasets"]["cat"]["originals"][0]
            self.assertEqual(original["path"], os.path.abspath(src))
            self.assertEqual(original["realPath"], str((d / "v1" / "SKILL.md").resolve()))
            link.unlink(); link.symlink_to(d / "v2")  # a new release
            from service import Service
            service = Service(config["db"], Path(config["registry"]), lambda *_: None)
            self.assertEqual(service.pointer("cat", "owner")[1]["status"], "preparation-required")


class PrincipalNameTests(unittest.TestCase):
    def test_agent_names_with_spaces_or_path_parts_are_refused(self):
        from service import valid_principal
        for good in ('primary', 'primary-helper', 'wheel_watchers', 'lead0923', 'a.b'):
            self.assertTrue(valid_principal(good), good)
        for bad in ('businessfi ', ' primary', 'x/../primary', '..', 'a b', '', '-x', 'a/b', None):
            self.assertFalse(valid_principal(bad), repr(bad))
