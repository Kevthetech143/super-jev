"""Saved answers have no clock expiry: they last until their source changes.

Promise item 2 of the saved-answer contract (docs/GETTING-STARTED.md, step 7).
"""
import time
import unittest
from unittest import mock

from test_regressions import Fixture


class SavedLifetimeTests(unittest.TestCase):

    def fixture(self, **kwargs):
        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.temp.cleanup)
        return fixture

    def test_default_saved_answer_still_hits_after_a_year(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        later = time.time() + 365 * 86400
        with mock.patch("service.time.time", return_value=later):
            got = fixture.service.cached("alice", "color?")
        self.assertEqual(got["status"], "verified-cache-hit")
        self.assertEqual(got["answer"], "Blue.")

    def test_changed_source_still_ends_the_saved_answer(self):
        fixture = self.fixture()
        fixture.approve(fixture.ticket())
        fixture.source.write_text("The launch color changed after review.\n")
        self.assertEqual(fixture.service.cached("alice", "color?")["status"], "cache-miss")

    def test_an_operator_can_still_set_an_explicit_ttl(self):
        fixture = self.fixture(cache_ttl_seconds=10)
        fixture.approve(fixture.ticket())
        with mock.patch("service.time.time", return_value=time.time() + 60):
            self.assertEqual(fixture.service.cached("alice", "color?")["status"], "cache-miss")


if __name__ == "__main__":
    unittest.main()
