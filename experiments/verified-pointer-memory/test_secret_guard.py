"""chk7 repro: a memory navigate (cli.py -> navigation-cli.ts -> Jev fetch) and a
connect never send or accept a secret. Fetch is mocked; no key is real."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cli import run
from path_connect import connect

HERE = Path(__file__).resolve().parent
NAV = ['node', str(HERE.parents[1] / 'src' / 'navigation-cli.ts')]
SECRET = 'api_key = sk-live-9fQ2xZ7pL0aBcD3eF4'


class SecretGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sends = self.root / 'sends.log'
        hook = self.root / 'hook.mjs'
        hook.write_text("import fs from 'node:fs';\nglobalThis.fetch = async (url, init) => { fs.appendFileSync("
                        + json.dumps(str(self.sends)) + ", 'FETCH ' + url + '\\n'); return new Response('{}', {status: 500}); };\n")
        self.env = {**os.environ, 'TYPESAFE_API_KEY': 'fake-key', 'NODE_OPTIONS': f'--import {hook}'}
        self.rec = self.root / 'rec.md'
        self.rec.write_text('# Rec\nThe box is blue.\n')
        self.config = {'db': str(self.root / 'db.sqlite'), 'registry': str(self.root / 'registry.json'),
                       'navigationCommand': NAV, 'retrievalCommand': NAV, 'providerTimeoutSeconds': 30,
                       'cacheTtlSeconds': 60, 'reviewTtlSeconds': 60, 'allowAgentAssist': False}

    def sent(self):
        return self.sends.read_text().count('FETCH') if self.sends.exists() else 0

    def test_navigate_with_secret_question_is_refused_before_node(self):
        request = {'action': 'navigate', 'pointer': 'recs', 'principal': 'me', 'question': 'what is ' + SECRET}
        with self.assertRaisesRegex(ValueError, 'contains a secret; not sent'):
            run(request, self.config)
        self.assertEqual(self.sent(), 0)

    def test_navigation_cli_itself_refuses_a_secret_body(self):
        # Even a caller that skips cli.py cannot send: the Jev fetch layer scans the body.
        def catalog(description):
            leaf = lambda n, d: {'id': n, 'label': n, 'description': d, 'sourceId': 'src-' + n}
            return {'version': 1, 'structure': 'flat-files', 'rootId': 'root', 'nodes': [
                {'id': 'root', 'label': 'root', 'description': 'recs', 'children': ['a', 'b']},
                leaf('a', description), leaf('b', 'The box is blue.')]}
        for payload in ({'question': 'what key?', 'catalog': catalog(SECRET)},
                        {'question': SECRET, 'catalog': catalog('rec')}):
            process = subprocess.run(NAV, input=json.dumps(payload), capture_output=True, text=True, env=self.env, timeout=60)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('contains a secret; not sent', process.stderr)
        self.assertEqual(self.sent(), 0)
        # Control: the same clean request does reach the mocked fetch.
        subprocess.run(NAV, input=json.dumps({'question': 'what colour?', 'catalog': catalog('rec')}),
                       capture_output=True, text=True, env=self.env, timeout=60)
        self.assertGreater(self.sent(), 0)

    def test_connect_refuses_a_file_with_a_secret(self):
        sec = self.root / 'sec.md'
        sec.write_text('# Keys\n' + SECRET + '\n')
        result = connect({'pointer': 'recs', 'principals': ['me'], 'sources': [{'path': str(self.rec)}, {'path': str(sec)}]}, self.config)
        self.assertEqual(result['reason'], 'secret-held')
        self.assertFalse(Path(self.config['registry']).exists())
        self.assertNotEqual(connect({'pointer': 'recs', 'principals': ['me'], 'sources': [{'path': str(self.rec)}]}, self.config).get('reason'), 'secret-held')


if __name__ == '__main__':
    unittest.main()
