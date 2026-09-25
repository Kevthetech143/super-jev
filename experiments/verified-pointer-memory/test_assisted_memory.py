"""Focused unit coverage for persistent, reviewed agent assistance."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from service import Service
from test_regressions import Fixture


class AssistedMemoryTests(unittest.TestCase):

    def fixture(self, **kwargs):
        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.temp.cleanup)
        return fixture

    def test_no_match_can_be_assisted_and_approved_by_evidence_id(self):
        fixture = self.fixture(allow_agent_assist=True)
        fixture.service.retrieve = lambda dataset, question: {'status': 'no-match'}
        original = fixture.service.search('docs', 'color?', 'alice')
        assisted = fixture.service.assist(
            original['attemptId'], 'alice', 'The registered source covers color.',
            [{'sourceId': 'one', 'startLine': 1, 'endLine': 1}], now=100)
        evidence_id = assisted['passages'][0]['evidenceId']
        fixture.service.approve(assisted['approvalTicket'], 'alice', 'Blue.',
                                [{'evidenceId': evidence_id}], approved=True,
                                now=100)
        hit = fixture.service.search('docs', 'color?', 'alice', now=101)
        self.assertEqual(hit['status'], 'verified-cache-hit')
        self.assertEqual(hit['resolution'], 'agent-assisted')
        self.assertEqual(hit['originatingAttemptId'], original['attemptId'])
        inspected = fixture.service.attempt(original['attemptId'], 'alice')
        self.assertEqual(inspected['retrievalStatus'], 'no-match')

    def test_open_cites_a_caller_ranked_file_without_retrieval(self):
        fixture = self.fixture(allow_agent_assist=True)
        fixture.service.retrieve = lambda *a: self.fail('open must not run retrieval')
        opened = fixture.service.open_attempt('docs', 'color?', 'alice')
        assisted = fixture.service.assist(
            opened['attemptId'], 'alice', 'ask() ranked this file first.',
            [{'sourceId': 'one', 'startLine': 1, 'endLine': 1}], now=100)
        fixture.service.approve(assisted['approvalTicket'], 'alice', 'Blue.',
                                [{'evidenceId': assisted['passages'][0]['evidenceId']}],
                                approved=True, now=100)
        del fixture.service.retrieve
        self.assertEqual(fixture.service.search('docs', 'color?', 'alice', now=101)['status'],
                         'verified-cache-hit')
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            fixture.service.assist(opened['attemptId'], 'bob', 'r',
                                   [{'sourceId': 'one', 'startLine': 1, 'endLine': 1}])
        disabled = self.fixture(allow_agent_assist=False)
        with self.assertRaisesRegex(ValueError, 'assist is disabled'):
            disabled.service.open_attempt('docs', 'color?', 'alice')

    def test_attempt_survives_remove_but_is_not_exposed(self):
        fixture = self.fixture(allow_agent_assist=True)
        result = fixture.service.search('docs', 'color?', 'alice')
        fixture.service.remove('docs')
        with fixture.service.connect() as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM attempts WHERE attempt=?',
                (result['attemptId'],)).fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            fixture.service.attempt(result['attemptId'], 'alice')
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            fixture.service.attempt(result['attemptId'], 'bob')

    def test_remove_and_reregister_does_not_resurrect_attempt_visibility(self):
        fixture = self.fixture(allow_agent_assist=True)
        result = fixture.service.search('docs', 'color?', 'alice')
        fixture.service.remove('docs')
        fixture.service.register('docs', 'test', ['alice'])
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            fixture.service.attempt(result['attemptId'], 'alice')
        with fixture.service.connect() as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM attempts WHERE attempt=?',
                (result['attemptId'],)).fetchone()[0], 1)

    def test_pointer_rebound_to_another_dataset_does_not_expose_old_attempt(self):
        fixture = self.fixture(allow_agent_assist=True)
        result = fixture.service.search('docs', 'color?', 'alice')
        registry = json.loads(fixture.registry.read_text())
        registry['datasets']['renamed'] = registry['datasets'].pop('test')
        fixture.registry.write_text(json.dumps(registry, sort_keys=True,
                                               separators=(',', ':')))
        fixture.service.register('docs', 'renamed', ['alice'])
        with self.assertRaisesRegex(ValueError, 'unauthorized'):
            fixture.service.attempt(result['attemptId'], 'alice')

    def test_v2_migration_preserves_cache_and_pending(self):
        fixture = self.fixture()
        ticket = fixture.ticket()
        fixture.approve(ticket)
        pending_ticket = fixture.ticket('another question')
        with sqlite3.connect(fixture.db) as connection:
            connection.execute("UPDATE schema_meta SET version='2'")
            connection.execute('DROP TABLE attempts')
            before_cache = connection.execute('SELECT * FROM cache').fetchall()
            before_pending = connection.execute('SELECT * FROM pending').fetchall()
        migrated = Service(fixture.db, fixture.registry, fixture.retrieve)
        with migrated.connect() as connection:
            self.assertEqual(connection.execute(
                'SELECT version FROM schema_meta').fetchone(), ('3',))
            self.assertEqual(connection.execute('SELECT * FROM cache').fetchall(),
                             before_cache)
            self.assertEqual(connection.execute('SELECT * FROM pending').fetchall(),
                             before_pending)
            self.assertEqual(before_pending[0][0], pending_ticket)

    def test_v2_migration_rejects_malformed_layout_without_mutation(self):
        fixture = self.fixture()
        variants = {
            'missing-check': (
                'CREATE TABLE schema_meta(singleton INTEGER PRIMARY KEY, version TEXT NOT NULL)',
                'CREATE INDEX cache_pointer ON cache(pointer)',
                'CREATE INDEX pending_pointer ON pending(pointer)'),
            'wrong-index': (
                'CREATE TABLE schema_meta(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version TEXT NOT NULL)',
                'CREATE UNIQUE INDEX cache_pointer ON cache(pointer)',
                'CREATE INDEX pending_pointer ON pending(pointer)'),
            'wrong-type': (
                'CREATE TABLE schema_meta(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), version TEXT NOT NULL)',
                'CREATE INDEX cache_pointer ON cache(pointer)',
                'CREATE INDEX pending_pointer ON pending(pointer)'),
        }
        for label, statements in variants.items():
            with self.subTest(label=label):
                database = fixture.root / f'{label}.sqlite'
                body_type = 'BLOB' if label == 'wrong-type' else 'TEXT'
                with sqlite3.connect(database) as connection:
                    connection.execute(statements[0])
                    connection.execute("INSERT INTO schema_meta VALUES (1,'2')")
                    connection.execute(
                        'CREATE TABLE pointers(name TEXT PRIMARY KEY, body TEXT NOT NULL)')
                    connection.execute(
                        f'CREATE TABLE cache(k TEXT PRIMARY KEY, pointer TEXT NOT NULL, generation TEXT NOT NULL, fingerprint TEXT NOT NULL, body {body_type} NOT NULL)')
                    connection.execute(
                        'CREATE TABLE pending(ticket TEXT PRIMARY KEY, pointer TEXT NOT NULL, generation TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL)')
                    connection.execute(statements[1])
                    connection.execute(statements[2])
                with sqlite3.connect(database) as connection:
                    before = list(connection.iterdump())
                with self.assertRaisesRegex(ValueError, 'invalid version 2'):
                    Service(database, fixture.registry, fixture.retrieve)
                with sqlite3.connect(database) as connection:
                    self.assertEqual(list(connection.iterdump()), before)

    def test_sources_are_bounded_and_authorized(self):
        fixture = self.fixture()
        result = fixture.service.sources('docs', 'alice')
        self.assertEqual(result['sources'][0]['sourceId'], 'one')
        self.assertEqual(result['sources'][0]['lineCount'], 1)
        self.assertEqual(result['sources'][0]['path'], str(fixture.source))
        with self.assertRaises(ValueError):
            fixture.service.sources('docs', 'alice', limit=101)

    def test_assist_rechecks_current_freshness_after_selection(self):
        fixture = self.fixture(allow_agent_assist=True)
        registry = json.loads(fixture.registry.read_text())
        registry['datasets']['test']['checkedAt'] = 90
        fixture.registry.write_text(json.dumps(registry, sort_keys=True,
                                               separators=(',', ':')))
        fixture.service.register('docs', 'test', ['alice'])
        fixture.service.retrieve = lambda dataset, question: {'status': 'no-match'}
        policy = {'mode': 'current', 'maxAgeSeconds': 30}
        original = fixture.service.search('docs', 'color?', 'alice', now=100,
                                          freshness=policy)
        clock = [100]
        real_pointer = fixture.service.pointer

        def delayed_pointer(name, principal):
            value = real_pointer(name, principal)
            clock[0] = 121
            return value

        fixture.service.pointer = delayed_pointer
        with patch('service.time.time', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(ValueError, 'refresh-required'):
                fixture.service.assist(
                    original['attemptId'], 'alice', 'Check registered evidence.',
                    [{'sourceId': 'one', 'startLine': 1, 'endLine': 1}])


if __name__ == '__main__':
    unittest.main()
