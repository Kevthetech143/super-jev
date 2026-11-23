import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from service import pointer_generations, principal_pointer_names  # noqa: E402


class NoJson:
    """A connection whose SQLite has no JSON functions: any statement using one fails the way SQLite does."""
    def __init__(self, c):
        self.c = c

    def execute(self, sql, *args):
        if 'json_' in sql:
            raise sqlite3.OperationalError('no such function: json_type')
        return self.c.execute(sql, *args)


BODIES = {
    'plain': '{"principals":["alice","bob"],"generation":"g1"}',
    'other': '{"principals":["carol"],"generation":"g2"}',
    # duplicate keys: json.loads keeps the LAST, SQLite JSON reads the FIRST
    'dup-last-alice': '{"principals":["carol"],"principals":["alice"],"generation":"g3"}',
    'dup-first-alice': '{"principals":["alice"],"principals":["carol"],"generation":"g4"}',
    'dup-generation': '{"principals":["alice"],"generation":"old","generation":"new"}',
    'nested-key': '{"principals":["carol"],"x":{"principals":["alice"]},"generation":"g5"}',
    'string-principals': '{"principals":"alice-and-more","generation":"g6"}',
    'no-principals': '{"generation":"g7"}',
    'gen-object': '{"principals":["alice"],"generation":{"a":1}}',
    'gen-array': '{"principals":["alice"],"generation":[1,2]}',
    'gen-number': '{"principals":["alice"],"generation":7}',
    'gen-null': '{"principals":["alice"],"generation":null}',
    'gen-bool': '{"principals":["alice"],"generation":true}',
    'gen-missing': '{"principals":["alice"]}',
    'unicode': '{"principals":["\\u00e9l\\u00e8ve"],"generation":"g8"}',
}


def reference_names(rows, principal):
    return sorted(n for n, b in rows if principal in json.loads(b).get('principals', []))


def reference_generations(rows):
    return {n: json.loads(b).get('generation') for n, b in rows}


class PointerBodyReads(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.execute('CREATE TABLE pointers(name TEXT PRIMARY KEY, body TEXT)')
        self.c.executemany('INSERT INTO pointers VALUES(?,?)', BODIES.items())
        self.rows = list(BODIES.items())

    def test_names_match_the_old_python_read_for_every_principal(self):
        for who in ('alice', 'bob', 'carol', 'nobody', 'alice-and', '\u00e9l\u00e8ve'):
            self.assertEqual(principal_pointer_names(self.c, who), reference_names(self.rows, who), who)

    def test_duplicate_principals_key_last_wins(self):
        names = principal_pointer_names(self.c, 'alice')
        self.assertIn('dup-last-alice', names)
        self.assertNotIn('dup-first-alice', names)

    def test_names_without_json_functions(self):
        for who in ('alice', 'carol'):
            self.assertEqual(principal_pointer_names(NoJson(self.c), who), reference_names(self.rows, who))

    def test_generations_match_the_old_python_read(self):
        self.assertEqual(pointer_generations(self.c), reference_generations(self.rows))

    def test_generation_object_array_number_and_duplicate_use_the_python_path(self):
        got = pointer_generations(self.c)
        self.assertEqual(got['gen-object'], {'a': 1})
        self.assertEqual(got['gen-array'], [1, 2])
        self.assertEqual(got['gen-number'], 7)
        self.assertEqual(got['dup-generation'], 'new')
        self.assertIsNone(got['gen-missing'])

    def test_generations_without_json_functions(self):
        self.assertEqual(pointer_generations(NoJson(self.c)), reference_generations(self.rows))


if __name__ == '__main__':
    unittest.main()
