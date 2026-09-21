"""Focused unit coverage for the cache-only `cached` action.

`cached()` must reuse the exact hit-validation path search() uses (via the
shared `_cache_lookup()` helper) and must never call retrieve() or
navigate_provider(). These tests assert both: the returned shape matches a
search() verified-cache-hit, and Fixture's retrieve() call counter never moves.
"""
import unittest

from test_regressions import Fixture


class CachedActionTests(unittest.TestCase):

    def fixture(self, **kwargs):
        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.temp.cleanup)
        return fixture

    def test_hit_matches_search_verified_cache_hit_shape_with_zero_provider_calls(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        calls_before = fixture.calls

        expected = fixture.service.search('docs', 'color?', 'alice')
        got = fixture.service.cached('alice', 'color?')

        self.assertEqual(fixture.calls, calls_before, 'cached() must never call retrieve()')
        self.assertEqual(got['status'], 'verified-cache-hit')
        self.assertEqual(got['answer'], expected['answer'])
        self.assertEqual(got['evidence'], expected['evidence'])
        self.assertEqual(got['freshness'], expected['freshness'])
        self.assertEqual(got['resolution'], expected['resolution'])

    def test_hit_found_by_fan_out_when_no_pointer_supplied(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        got = fixture.service.cached('alice', 'color?', pointer=None)
        self.assertEqual(got['status'], 'verified-cache-hit')
        self.assertEqual(got['answer'], 'Blue.')

    def test_miss_when_no_approved_answer_and_no_provider_call_made(self):
        fixture = self.fixture()
        calls_before = fixture.calls
        got = fixture.service.cached('alice', 'color?')
        self.assertEqual(got, {'status': 'cache-miss', 'checked': ['docs']})
        self.assertEqual(fixture.calls, calls_before)

    def test_stale_source_after_approval_becomes_a_miss_not_a_leak(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        calls_before = fixture.calls
        # Change the reviewed source after approval; this changes the dataset
        # snapshot digest so pointer() reports preparation-required.
        fixture.source.write_text('The launch color changed after review.\n')

        got = fixture.service.cached('alice', 'color?')

        self.assertEqual(got['status'], 'cache-miss')
        self.assertIn('docs', got['checked'])
        self.assertEqual(fixture.calls, calls_before)

    def test_wrong_principal_pointer_is_not_visible_in_fan_out(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        got = fixture.service.cached('bob', 'color?')
        self.assertEqual(got, {'status': 'cache-miss', 'checked': []})

    def test_wrong_principal_explicit_pointer_does_not_leak_answer(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        got = fixture.service.cached('bob', 'color?', pointer='docs')
        self.assertEqual(got['status'], 'cache-miss')
        self.assertEqual(got['checked'], ['docs'])

    def test_fan_out_checks_every_visible_pointer_and_hits_the_right_one(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        # Register a second pointer at the same dataset for alice; it has no
        # approved answer for a different question.
        fixture.service.register('docs-two', 'test', ['alice'])

        got = fixture.service.cached('alice', 'color?')
        self.assertEqual(got['status'], 'verified-cache-hit')

        miss = fixture.service.cached('alice', 'unasked question?')
        self.assertEqual(miss['status'], 'cache-miss')
        self.assertEqual(set(miss['checked']), {'docs', 'docs-two'})

    def test_unknown_explicit_pointer_is_a_miss_not_an_error(self):
        fixture = self.fixture()
        got = fixture.service.cached('alice', 'color?', pointer='no-such-pointer')
        self.assertEqual(got, {'status': 'cache-miss', 'checked': ['no-such-pointer']})

    def test_missing_principal_or_question_is_rejected(self):
        fixture = self.fixture()
        with self.assertRaises(ValueError):
            fixture.service.cached('', 'color?')
        with self.assertRaises(ValueError):
            fixture.service.cached('alice', '')


if __name__ == '__main__':
    unittest.main()
