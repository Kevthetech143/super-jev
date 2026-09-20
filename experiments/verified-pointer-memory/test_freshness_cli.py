"""Exercise the public freshness flow through subprocess CLI, with no Jev calls."""
import json
import time
import unittest

import test_cli


class FreshnessCliTests(unittest.TestCase):
    def setUp(self):
        self.f = test_cli.PublicCliTests()
        self.f.setUpClass()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.registry, self.manifest = self.f.dataset()
        source = json.loads(self.manifest.read_text())['sources'][0]
        response = {'status': 'ready', 'passages': [{
            'sourceId': 'policy', 'path': source['path'], 'contentSHA': source['contentSHA'],
            'startLine': 1, 'endLine': 1,
            'reviewedText': 'The synthetic launch policy is blue.\n'}]}
        command, self.calls = self.f.provider(json.dumps(response))
        self.config = self.f.config(self.registry, command)
        self.f.register(self.config)

    def action(self, body):
        request = self.f.write_json('fresh-action.json', body)
        return self.f.cli('--config', str(self.config), '--input', str(request))

    def search(self, **extra):
        return self.action({'action': 'search', 'pointer': 'public-docs',
                            'question': 'What is the current launch policy?',
                            'principal': 'alice', **extra})

    def refresh(self):
        registry = json.loads(self.registry.read_text())
        registry['datasets']['synthetic-reviewed']['checkedAt'] = time.time()
        self.registry.write_text(json.dumps(registry))
        self.f.register(self.config)

    def test_refresh_review_reuse_and_expiration_flow(self):
        policy = {'mode': 'current', 'maxAgeSeconds': 60}
        _, blocked = self.search(freshness=policy)
        self.assertEqual(blocked['status'], 'refresh-required')
        self.assertEqual(blocked['nextAction'], 'refresh-source-and-preparation')
        self.assertFalse(self.calls.exists())
        self.refresh()
        _, ready = self.search(freshness=policy)
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(ready['freshness']['mode'], 'current')
        _, saved = self.action({'action': 'approve', 'ticket': ready['approvalTicket'],
             'principal': 'alice', 'approved': True, 'answer': 'The policy is blue.',
             'evidence': [{'sourceId': 'policy', 'quote': 'blue'}]})
        self.assertEqual(saved['status'], 'saved')
        _, hit = self.search(freshness=policy)
        self.assertEqual(hit['status'], 'verified-cache-hit')
        _, expired = self.search(freshness={'mode': 'current', 'maxAgeSeconds': 0.000001})
        self.assertEqual(expired['status'], 'refresh-required')
        self.assertEqual(self.calls.read_text().splitlines(), ['call'])

    def test_bad_freshness_policy_is_structured_error(self):
        for policy in [None, [], {'mode': 'current'}, {'mode': 'unknown'}]:
            with self.subTest(policy=policy):
                process, result = self.search(freshness=policy)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(result['status'], 'error')
        self.assertFalse(self.calls.exists())

    def test_corrupt_database_returns_structured_error(self):
        (self.f.root / 'state.sqlite').write_bytes(b'invalid SQLite bytes')
        process, result = self.search()
        self.assertEqual(process.returncode, 1)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
