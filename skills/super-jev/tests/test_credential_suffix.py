"""Credential-suffix files are never connected. Offline."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('pb_cred', Path(__file__).resolve().parents[1] / 'prepare_bulk.py')
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def test_credential_target_and_compound_name_held_even_with_override(tmp_path):
    target = tmp_path / 'server.pem'
    target.write_text('an unrecognized credential encoding')
    (tmp_path / 'ordinary.md').symlink_to(target)
    (tmp_path / 'backup.key.md').write_text('another unrecognized encoding')
    files, held = pb.inventory([tmp_path])
    assert not files
    assert len(held) == 2 and all('cannot be overridden' in why for _, why in held)
