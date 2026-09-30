"""Registry is parsed once per unchanged file; changes and in-process writes are never stale."""
import json
import unittest
from unittest import mock

import service
from test_regressions import Fixture


class RegistryCacheTests(unittest.TestCase):

    def fixture(self):
        fixture = Fixture()
        self.addCleanup(fixture.temp.cleanup)
        return fixture

    def parses(self, fixture, action):
        real = json.loads
        with mock.patch.object(service.json, 'loads', side_effect=real) as spy:
            action()
        return sum(1 for c in spy.call_args_list if b'"datasets"' in (c.args[0] if isinstance(c.args[0], bytes) else b''))

    def test_repeated_snapshot_parses_once(self):
        f = self.fixture()
        service.invalidate_registry(f.registry)
        n = self.parses(f, lambda: [f.service.snapshot('test') for _ in range(5)])
        self.assertEqual(n, 1)

    def test_changed_file_reparses(self):
        f = self.fixture()
        f.service.snapshot('test')
        data = json.loads(f.registry.read_text())
        data['datasets']['test']['note'] = 'changed'
        f.registry.write_text(json.dumps(data))
        self.assertEqual(f.service.snapshot('test')['entry']['note'], 'changed')

    def test_write_then_read_sees_new_data_after_atomic_replace(self):
        import path_connect
        f = self.fixture()
        f.service.snapshot('test')
        data = json.loads(f.registry.read_text())
        data['datasets']['test']['note'] = 'new'
        path_connect._atomic(f.registry, data)
        self.assertEqual(f.service.snapshot('test')['entry']['note'], 'new')

    def test_callers_cannot_mutate_shared_registry(self):
        f = self.fixture()
        snap = f.service.snapshot('test')
        snap['entry']['originals'][0]['sha256'] = 'tampered'
        snap['entry']['injected'] = True
        again = f.service.snapshot('test')
        self.assertNotEqual(again['entry']['originals'][0]['sha256'], 'tampered')
        self.assertNotIn('injected', again['entry'])


if __name__ == '__main__':
    unittest.main()
