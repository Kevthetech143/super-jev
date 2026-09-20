"""Lifecycle checks: injected provider, actual files/SQLite, deterministic clocks."""
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from test_regressions import Fixture
from service import pack, sha


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(cache_ttl_seconds=1000)
        self.addCleanup(self.f.temp.cleanup)
        self.s = self.f.service
        self.current = {'mode': 'current', 'maxAgeSeconds': 30}

    def checked(self, value):
        registry = json.loads(self.f.registry.read_text())
        registry['datasets']['test']['checkedAt'] = value
        self.f.registry.write_text(pack(registry))
        self.s.register('docs', 'test', ['alice'])

    def search(self, now=100, **kwargs):
        return self.s.search('docs', 'color?', 'alice', now=now, **kwargs)

    def approve(self, result, now=100):
        self.s.approve(result['approvalTicket'], 'alice', 'Blue.',
                       [{'sourceId': 'one', 'quote': 'blue'}], approved=True, now=now)

    def test_snapshot_compatibility_and_context_isolation(self):
        self.approve(self.search())
        self.assertEqual(self.search()['status'], 'verified-cache-hit')
        self.assertEqual(self.search(context='different person/date')['status'], 'ready')
        self.assertEqual(self.f.calls, 2)

    def test_changed_deleted_or_corrupt_registered_material_blocks_cache(self):
        self.approve(self.search())
        for path, changed in [(self.f.source, b'new facts'), (self.f.source, None),
                              (self.f.manifest, b'{bad json'), (self.f.registry, b'{bad json')]:
            with self.subTest(path=path.name, changed=changed):
                original = path.read_bytes()
                if changed is None:
                    path.unlink()
                else:
                    path.write_bytes(changed)
                try:
                    self.assertEqual(self.search()['status'], 'preparation-required')
                    self.assertEqual(self.f.calls, 1)
                finally:
                    path.write_bytes(original)

    def test_current_requires_upstream_check_even_with_snapshot_cache(self):
        self.approve(self.search())
        self.assertEqual(self.search(freshness=self.current)['status'], 'refresh-required')
        self.assertEqual(self.f.calls, 1)

    def test_current_reuse_expires_at_source_deadline(self):
        self.checked(90)
        first = self.search(freshness=self.current)
        self.assertEqual(first['status'], 'ready')
        self.approve(first)
        self.assertEqual(self.search(now=119, freshness=self.current)['status'], 'verified-cache-hit')
        self.assertEqual(self.search(now=120, freshness=self.current)['status'], 'refresh-required')
        self.assertEqual(self.f.calls, 1)

    def test_policy_change_never_reuses_permissive_answer(self):
        self.checked(90)
        self.approve(self.search(freshness=self.current))
        strict = {'mode': 'current', 'maxAgeSeconds': 5}
        self.assertEqual(self.search(freshness=strict)['status'], 'refresh-required')
        self.assertEqual(self.search()['status'], 'ready')

    def test_future_or_non_numeric_upstream_check_is_not_fresh(self):
        for value in [101, True, '100', float('nan'), float('inf')]:
            with self.subTest(value=value):
                self.checked(value)
                result = self.search(freshness=self.current)
                self.assertEqual(result['status'], 'refresh-required')
        self.assertEqual(self.f.calls, 0)

    def test_invalid_policies_fail_before_provider(self):
        invalid = [None, [], 'current', {}, {'mode': 'mystery'},
                   {'mode': 'current'}, {'mode': 'current', 'maxAgeSeconds': 0},
                   {'mode': 'current', 'maxAgeSeconds': True},
                   {'mode': 'current', 'maxAgeSeconds': float('nan')},
                   {'mode': 'current', 'maxAgeSeconds': float('inf')},
                   {'mode': 'snapshot', 'surprise': True}]
        # None means omitted/default in the Python API; JSON null handling is tested by CLI.
        for policy in invalid[1:]:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    self.search(freshness=policy)
        self.assertEqual(self.f.calls, 0)

    def test_approval_after_source_deadline_is_rejected(self):
        self.checked(90)
        result = self.search(freshness=self.current)
        with self.assertRaises(ValueError):
            self.approve(result, now=120)
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_refresh_rotates_generation_and_requires_new_approval(self):
        self.checked(90)
        self.approve(self.search(freshness=self.current))
        self.checked(125)
        result = self.search(now=126, freshness=self.current)
        self.assertEqual(result['status'], 'ready')
        self.approve(result, now=126)
        self.assertEqual(self.search(now=127, freshness=self.current)['status'], 'verified-cache-hit')
        self.assertEqual(self.f.calls, 2)

    def test_revocation_blocks_saved_answer(self):
        self.approve(self.search())
        self.s.register('docs', 'test', ['bob'])
        self.assertEqual(self.search()['status'], 'access-denied')
        self.assertEqual(self.f.calls, 1)

    def test_unregistered_new_file_is_not_automatically_discovered(self):
        self.approve(self.search())
        (self.f.root / 'new-unregistered.txt').write_text('The color is now red.')
        self.assertEqual(self.search()['status'], 'verified-cache-hit')
        self.assertEqual(self.search(freshness=self.current)['status'], 'refresh-required')

    def test_source_deadline_crossed_during_provider_blocks_ready(self):
        self.checked(90)
        # The initial check is at 100; the provider returns after the deadline.
        clock = [100]
        retrieve = self.s.retrieve
        def slow_retrieve(dataset, question):
            result = retrieve(dataset, question)
            clock[0] = 121
            return result
        self.s.retrieve = slow_retrieve
        with patch('service.time.time', side_effect=lambda: clock[0]):
            result = self.s.search('docs', 'color?', 'alice', freshness=self.current)
        self.assertEqual(result['status'], 'refresh-required')
        self.assertEqual(self.f.calls, 1)
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM pending').fetchone()[0], 0)

    def test_source_deadline_crossed_during_cache_validation_blocks_hit(self):
        self.checked(90)
        self.approve(self.search(freshness=self.current))
        clock = [100]
        pointer = self.s.pointer
        def slow_pointer(name, principal):
            result = pointer(name, principal)
            clock[0] = 121
            return result
        self.s.pointer = slow_pointer
        with patch('service.time.time', side_effect=lambda: clock[0]):
            result = self.s.search('docs', 'color?', 'alice', freshness=self.current)
        self.assertEqual(result['status'], 'refresh-required')
        self.assertEqual(self.f.calls, 1)

    def test_approval_rechecks_deadline_after_sqlite_write_lock(self):
        self.checked(90)
        first = self.search(freshness=self.current)
        clock = [100]
        original_connect = self.s.connect
        class DelayedConnection:
            def __init__(self, connection):
                self.connection = connection
            def execute(self, statement, *args):
                result = self.connection.execute(statement, *args)
                if statement == 'BEGIN IMMEDIATE':
                    clock[0] = 121
                return result
        @contextmanager
        def delayed_connect():
            with original_connect() as connection:
                yield DelayedConnection(connection)
        self.s.connect = delayed_connect
        with patch('service.time.time', side_effect=lambda: clock[0]):
            with self.assertRaises(ValueError):
                self.s.approve(first['approvalTicket'], 'alice', 'Blue.',
                    [{'sourceId': 'one', 'quote': 'blue'}], approved=True)
        with original_connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM pending').fetchone()[0], 1)

    def test_corrupt_sqlite_is_not_a_valid_answer(self):
        self.approve(self.search())
        self.f.db.write_bytes(b'not a sqlite database')
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            self.search()


if __name__ == '__main__':
    unittest.main()
