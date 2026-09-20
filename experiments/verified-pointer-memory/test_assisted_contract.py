"""Independent public-CLI recovery tests: real storage, injected retrieval."""
import json
import sqlite3
import unittest

import test_cli


class AssistedContractTests(unittest.TestCase):
    def setUp(self):
        self.f = test_cli.PublicCliTests()
        self.f.setUpClass()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.registry, self.manifest = self.f.dataset()
        command, self.calls = self.f.provider(json.dumps({'status': 'no-match', 'trace': []}))
        self.config = self.f.config(self.registry, command, allowAgentAssist=True)
        self.f.register(self.config)

    def action(self, **body):
        request = self.f.write_json('action.json', body)
        process, result = self.f.cli('--config', str(self.config), '--input', str(request))
        return result

    def search(self, context='release-A'):
        return self.action(action='search', pointer='public-docs', principal='alice',
                           question='What is the launch policy?', context=context)

    def assist(self, initial, **overrides):
        request = dict(action='assist', attemptId=initial['attemptId'], principal='alice',
                       reason='Independent inspection located the reviewed policy.',
                       references=[{'sourceId': 'policy', 'startLine': 1, 'endLine': 1}])
        request.update(overrides)
        return self.action(**request)

    def approve(self, ready, **overrides):
        request = dict(action='approve', ticket=ready['approvalTicket'], principal='alice',
                       approved=True, answer='The launch policy is blue.',
                       evidence=[{'evidenceId': ready['passages'][0]['evidenceId']}])
        request.update(overrides)
        return self.action(**request)

    def test_real_cli_miss_assist_review_cache_preserves_original_miss(self):
        initial = self.search()
        self.assertEqual(initial['status'], 'no-match')
        self.assertTrue(initial['attemptId'])
        ready = self.assist(initial)
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(self.calls.read_text().splitlines(), ['call'])
        with sqlite3.connect(self.f.root / 'state.sqlite') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0], 0)
        self.assertEqual(self.approve(ready)['status'], 'saved')
        hit = self.search()
        self.assertEqual(hit['status'], 'verified-cache-hit')
        self.assertEqual(hit['resolution'], 'agent-assisted')
        self.assertEqual(hit['originatingAttemptId'], initial['attemptId'])
        inspection = self.action(action='attempt', attemptId=initial['attemptId'], principal='alice')
        # Original observation must survive even after a successful rescue.
        self.assertIn('no-match', json.dumps(inspection))
        self.assertEqual(self.calls.read_text().splitlines(), ['call'])

    def test_context_change_cannot_reuse_assisted_answer(self):
        ready = self.assist(self.search())
        self.assertEqual(self.approve(ready)['status'], 'saved')
        self.assertEqual(self.search(context='release-B')['status'], 'no-match')
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_unknown_evidence_never_saves_answer(self):
        ready = self.assist(self.search())
        bad = self.approve(ready, evidence=[{'evidenceId': 'invented-not-returned'}])
        self.assertEqual(bad['status'], 'error')
        with sqlite3.connect(self.f.root / 'state.sqlite') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_wrong_principal_cannot_assist_or_inspect(self):
        initial = self.search()
        for result in [self.assist(initial, principal='bob'),
                       self.action(action='attempt', attemptId=initial['attemptId'], principal='bob')]:
            self.assertNotIn(result['status'], ['ready', 'verified-cache-hit', 'ok'])
            self.assertNotIn('The synthetic launch policy is blue.', json.dumps(result))

    def test_disabled_assistance_does_not_expose_recovery_evidence(self):
        initial = self.search()
        config = json.loads(self.config.read_text());config['allowAgentAssist'] = False
        self.config.write_text(json.dumps(config))
        result = self.assist(initial)
        self.assertNotEqual(result['status'], 'ready')
        self.assertNotIn('The synthetic launch policy is blue.', json.dumps(result))

    def test_source_mutation_blocks_assistance_and_approval(self):
        initial = self.search()
        ready = self.assist(initial)
        source = self.f.root / 'reviewed-policy.txt'
        source.write_text('The policy changed to red.')
        self.assertNotEqual(self.assist(initial)['status'], 'ready')
        self.assertEqual(self.approve(ready)['status'], 'error')
        with sqlite3.connect(self.f.root / 'state.sqlite') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_unknown_and_out_of_range_references_are_not_accepted(self):
        initial = self.search()
        for references in [[{'sourceId':'unknown','startLine':1,'endLine':1}],
                           [{'sourceId':'policy','startLine':10000,'endLine':10001}],
                           [{'sourceId':'policy','startLine':True,'endLine':1}],
                           [{'sourceId':'policy','startLine':1,'endLine':0}], []]:
            with self.subTest(references=references):
                self.assertNotEqual(self.assist(initial, references=references)['status'], 'ready')
        self.assertEqual(self.calls.read_text().splitlines(), ['call'])

    def test_reregister_invalidates_assistance_but_retains_audit(self):
        initial = self.search()
        self.f.register(self.config)
        self.assertNotEqual(self.assist(initial)['status'], 'ready')
        with sqlite3.connect(self.f.root / 'state.sqlite') as c:
            # Never assume the audit is a replacement of the old result with success.
            dump='\n'.join(c.iterdump())
        self.assertIn(initial['attemptId'], dump)
        self.assertIn('no-match', dump)

    def test_explicit_disapproval_does_not_cache(self):
        ready = self.assist(self.search())
        self.assertEqual(self.approve(ready, approved=False)['status'], 'error')
        self.assertEqual(self.search()['status'], 'no-match')


if __name__ == '__main__':
    unittest.main()
