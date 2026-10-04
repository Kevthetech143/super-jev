"""cached() must skip the per-pointer snapshot for pointers with no cache row."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_regressions import Fixture  # noqa: E402


class CachedSkipSnapshotTests(unittest.TestCase):

    def fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.temp.cleanup)
        snapped = []
        real = fixture.service.pointer

        def spy(name, principal):
            snapped.append(name)
            return real(name, principal)

        fixture.service.pointer = spy
        return fixture, snapped

    def test_pointer_with_no_row_is_not_snapshotted(self):
        fixture, snapped = self.fixture()
        got = fixture.service.cached('alice', 'color?')
        self.assertEqual(got, {'status': 'cache-miss', 'checked': ['docs']})
        self.assertEqual(snapped, [])

    def test_pointer_with_row_for_different_question_is_still_snapshotted(self):
        fixture, snapped = self.fixture()
        fixture.approve(fixture.ticket())
        snapped.clear()
        got = fixture.service.cached('alice', 'other question?')
        self.assertEqual(got['status'], 'cache-miss')
        self.assertEqual(snapped, ['docs'])

    def test_edited_source_with_row_still_returns_stale(self):
        fixture, snapped = self.fixture()
        fixture.approve(fixture.ticket())
        snapped.clear()
        fixture.source.write_text('The launch color changed after review.\n')
        got = fixture.service.cached('alice', 'color?')
        self.assertEqual(got['status'], 'cache-miss')
        self.assertEqual(snapped, ['docs'])


if __name__ == '__main__':
    unittest.main()
