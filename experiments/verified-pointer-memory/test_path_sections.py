"""A big file connected as line-range sections: each section is its own reviewed source,
read from the unchanged file, and routing names the file and its lines."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from path_connect import connect
from service import Service


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class SectionConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.big = self.root / 'big.md'
        self.lines = ['# Big notes\n', 'intro line\n', '## Alpha\n', 'alpha fact\n', '## Beta\n', 'beta fact\n']
        self.big.write_text(''.join(self.lines))
        self.config = {'db': str(self.root / 'answers.sqlite'), 'registry': str(self.root / 'registry.json')}
        self.request = {'pointer': 'notes', 'principals': ['owner'], 'sources': [
            {'path': str(self.big), 'lines': [1, 2], 'description': 'Big notes intro'},
            {'path': str(self.big), 'lines': [3, 4], 'description': 'Alpha section'},
            {'path': str(self.big), 'lines': [5, 6], 'description': 'Beta section'}]}

    def reviewed(self, request=None):
        request = request or self.request
        preview = connect(request, self.config)
        self.assertEqual(preview.get('reason'), 'review-required', preview)
        return {**request, 'reviewed': True, 'sources': preview['sources']}

    def service(self, navigate=None):
        return Service(self.config['db'], self.config['registry'], lambda *_: {'status': 'no-match'},
                       navigate_provider=navigate)

    def test_preview_hashes_each_section_and_the_file(self):
        preview = connect(self.request, self.config)
        whole = hashlib.sha256(self.big.read_bytes()).hexdigest()
        self.assertEqual([s['lines'] for s in preview['sources']], [[1, 2], [3, 4], [5, 6]])
        self.assertEqual({s['sha256'] for s in preview['sources']}, {whole})
        self.assertEqual(preview['sources'][1]['viewSHA'], sha('## Alpha\nalpha fact\n'))
        self.assertEqual(preview['bytes'], self.big.stat().st_size)  # the file counts once
        labels = [n['label'] for n in preview['catalog']['nodes'] if 'sourceId' in n]
        self.assertEqual(labels, ['big.md (lines 1-2)', 'big.md (lines 3-4)', 'big.md (lines 5-6)'])

    def test_sections_register_their_own_text_and_leave_the_file_unchanged(self):
        before = self.big.read_bytes()
        result = connect(self.reviewed(), self.config)
        self.assertEqual(result['status'], 'registered', result)
        self.assertEqual(self.big.read_bytes(), before)
        entry = self.service().snapshot('notes')['entry']
        manifest = json.loads(Path(entry['manifestPath']).read_text())
        self.assertEqual([Path(s['path']).read_text() for s in manifest['sources']],
                         ['# Big notes\nintro line\n', '## Alpha\nalpha fact\n', '## Beta\nbeta fact\n'])
        self.assertEqual([s['lines'] for s in manifest['sources']], [[1, 2], [3, 4], [5, 6]])
        self.assertEqual(len(entry['originals']), 1)  # one file, one staleness check
        self.assertEqual([s['lines'] for s in entry['recipe']['sources']], [[1, 2], [3, 4], [5, 6]])
        self.assertIsNone(self.service().pointer('notes', 'owner')[1])
        # Any edit to the file stales the pointer, like a whole file.
        self.big.write_text(''.join(self.lines) + 'more\n')
        self.assertEqual(self.service().pointer('notes', 'owner')[1]['status'], 'preparation-required')

    def test_navigation_candidate_names_the_section(self):
        connect(self.reviewed(), self.config)

        def navigate(question, catalog, limits):
            leaf = next(n for n in catalog['nodes'] if n.get('label') == 'big.md (lines 3-4)')
            return {'status': 'candidates', 'calls': 1, 'trace': [], 'complete': False, 'message': 'ok',
                    'candidates': [{'sourceId': leaf['sourceId'], 'nodeId': leaf['id'],
                                    'path': ['root', leaf['id']], 'score': 0.9}]}
        out = self.service(navigate).navigate('notes', 'owner', 'alpha?')
        self.assertEqual(out['candidates'][0]['originalPath'], str(self.big))
        self.assertEqual(out['candidates'][0]['lines'], [3, 4])
        rows = self.service().sources('notes', 'owner')['sources']
        self.assertEqual([r.get('lines') for r in rows], [[1, 2], [3, 4], [5, 6]])

    def test_a_section_needs_its_own_reviewed_hash(self):
        req = self.reviewed()
        req['sources'][1] = {k: v for k, v in req['sources'][1].items() if k != 'viewSHA'}
        self.assertEqual(connect(req, self.config)['reason'], 'review-required')
        req = self.reviewed()
        req['sources'][1]['viewSHA'] = sha('something else\n')
        self.assertEqual(connect(req, self.config)['reason'], 'review-required')
        self.assertFalse(Path(self.config['registry']).exists())

    def test_bad_ranges_are_refused(self):
        for lines, reason in (([2, 1], 'invalid-lines'), ([0, 1], 'invalid-lines'), ([1, 99], 'invalid-lines'),
                              ([1], 'invalid-lines'), ([True, 2], 'invalid-lines'), ([2, 3], 'duplicate-source')):
            req = json.loads(json.dumps(self.request))
            req['sources'][1]['lines'] = lines
            self.assertEqual(connect(req, self.config)['reason'], reason, lines)
        whole_and_part = {**self.request, 'sources': [{'path': str(self.big)}, self.request['sources'][1]]}
        self.assertEqual(connect(whole_and_part, self.config)['reason'], 'duplicate-source')
        part_and_whole = {**self.request, 'sources': [self.request['sources'][1], {'path': str(self.big)}]}
        self.assertEqual(connect(part_and_whole, self.config)['reason'], 'duplicate-source')
        with_view = json.loads(json.dumps(self.request))
        with_view['sources'][0]['viewTransform'] = {'version': 1, 'operations': [{'op': 'redact-emails'}]}
        self.assertEqual(connect(with_view, self.config)['reason'], 'invalid-lines')

    def test_blank_section_and_secret_section_are_refused(self):
        self.big.write_text('# T\n\n\nfact\n')
        req = {**self.request, 'sources': [{'path': str(self.big), 'lines': [2, 3]}]}
        self.assertEqual(connect(req, self.config)['reason'], 'empty-view')
        self.big.write_text('# T\nthe pass' + 'word is hunter2' + 'xyz9\n')
        req = {**self.request, 'sources': [{'path': str(self.big), 'lines': [2, 2]}]}
        self.assertEqual(connect(req, self.config)['reason'], 'secret-held')


if __name__ == '__main__':
    unittest.main()
