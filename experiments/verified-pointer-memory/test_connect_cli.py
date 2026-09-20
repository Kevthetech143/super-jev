"""Public connect action works from raw file paths without hand-made manifests."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).with_name('cli.py')


class ConnectCliTests(unittest.TestCase):
    def test_preview_confirm_panel_and_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'record.md'
            source.write_text('# A record\nA factual source sentence.\n')
            config = root / 'config.json'
            config.write_text(json.dumps({'db': str(root / 'db.sqlite'), 'registry': str(root / 'registry.json')}))
            request = root / 'request.json'

            def call(body):
                request.write_text(json.dumps(body))
                proc = subprocess.run([sys.executable, str(CLI), '--config', str(config), '--input', str(request)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                return json.loads(proc.stdout)

            body = {'action': 'connect', 'pointer': 'records', 'principals': ['owner'], 'sources': [{'path': str(source)}]}
            preview = call(body)
            self.assertEqual(preview['status'], 'preparation-required')
            self.assertIn('Your files were found', preview['message'])
            self.assertEqual(preview['nextAction'], 'review-local-sources-then-connect')
            self.assertEqual(preview['gettingStarted']['requestTemplate']['action'], 'connect')
            self.assertFalse((root / 'db.sqlite').exists())
            self.assertFalse((root / 'registry.json').exists())
            confirmed = call({**body, 'sources': preview['sources'], 'reviewed': True})
            self.assertEqual(confirmed['status'], 'registered')
            panel = call({'action': 'panel', 'principal': 'owner'})
            self.assertEqual(panel['pointers'][0]['snapshotStatus'], 'available')
            self.assertIn('connect', panel['actions'])
            source.write_text('Changed original.')
            result = call({'action': 'search', 'pointer': 'records', 'principal': 'owner', 'question': 'What is the fact?'})
            self.assertEqual(result['status'], 'preparation-required')
            self.assertEqual(result['hints'][0]['action'], 'connect')


if __name__ == '__main__':
    unittest.main()


class ConnectShortcutTests(unittest.TestCase):
    def test_copyable_preview_confirmation_and_changed_bytes(self):
        import shlex
        with tempfile.TemporaryDirectory(prefix='connect space ') as folder:
            root = Path(folder)
            source = root / 'record with spaces.md'
            source.write_text('A reviewed test record.\n')
            config = root / 'config.json'
            config.write_text(json.dumps({'db': str(root / 'db.sqlite'), 'registry': str(root / 'registry.json')}))
            cmd = [sys.executable, str(CLI), '--config', str(config), '--connect', 'records', '--file', str(source), '--principal', 'owner']
            preview = json.loads(subprocess.check_output(cmd, text=True))
            self.assertEqual(preview['reason'], 'review-required')
            self.assertFalse((root / 'db.sqlite').exists())
            confirmation = shlex.split(preview['confirmCommand'])
            source.write_text('Changed before approval.\n')
            changed = json.loads(subprocess.check_output(confirmation, text=True))
            self.assertEqual(changed['reason'], 'review-required')
            self.assertFalse((root / 'db.sqlite').exists())
            registered = json.loads(subprocess.check_output(shlex.split(changed['confirmCommand']), text=True))
            self.assertEqual(registered['status'], 'registered')
