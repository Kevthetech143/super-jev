"""Versioned, bounded redaction recipes. No commands, imports or caller regexes."""
import hashlib
import json
import re

MARKERS = {'[REDACTED]', '[PERSON_REDACTED]', '[LOCAL_PATH_REDACTED]', '[EMAIL_REDACTED]'}
LOCAL_PATH = re.compile(r'(?:/Users/|/home/|~/)[^\s`\"\'<>]+')
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def derive(text, policy):
    """Return derived UTF-8 bytes and policy digest; reject unknown policy shapes."""
    if not isinstance(policy, dict) or set(policy) != {'version', 'operations'} or type(policy['version']) is not int or policy['version'] != 1:
        raise ValueError('unsupported view policy')
    operations = policy['operations']
    if not isinstance(operations, list) or not 1 <= len(operations) <= 32:
        raise ValueError('invalid view operations')
    encoded = json.dumps(policy, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    if len(encoded) > 65536:
        raise ValueError('view policy too large')
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError('invalid view operation')
        op = operation.get('op')
        if op in ('redact-local-paths', 'redact-emails'):
            if set(operation) != {'op'}:
                raise ValueError('invalid built-in operation')
            pattern, marker = (LOCAL_PATH, '[LOCAL_PATH_REDACTED]') if op == 'redact-local-paths' else (EMAIL, '[EMAIL_REDACTED]')
            text = pattern.sub(marker, text)
        elif op in ('redact-literals', 'drop-lines-containing'):
            expected = {'op', 'values', 'replacement'} if op == 'redact-literals' else {'op', 'values'}
            values = operation.get('values')
            if (set(operation) != expected or not isinstance(values, list) or not 1 <= len(values) <= 256
                    or any(not isinstance(v, str) or not v or len(v) > 4096 or '\n' in v or '\r' in v for v in values)):
                raise ValueError('invalid literal operation')
            if op == 'redact-literals':
                if operation['replacement'] not in MARKERS:
                    raise ValueError('invalid redaction marker')
                for value in sorted(set(values), key=lambda v: (-len(v), v)):
                    text = text.replace(value, operation['replacement'])
            else:
                # The chunker defines lines with LF only. Unicode separators inside one
                # such line must never detach hidden text from its drop marker.
                text = '\n'.join(line for line in text.split('\n') if not any(v in line for v in values))
        else:
            raise ValueError('unknown view operation')
        if len(text.encode()) > 5 * 1024 * 1024:
            raise ValueError('derived view too large')
    return text.encode(), hashlib.sha256(encoded).hexdigest()


if __name__ == '__main__':
    import argparse
    import os
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Derive a local view for review; never connects or calls a provider.')
    parser.add_argument('--source', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        with Path(args.source).expanduser().open('rb') as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('source too large')
        view, policy_sha = derive(raw.decode('utf-8'), json.loads(Path(args.policy).read_text()))
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(view)
        print(json.dumps({'sha256': hashlib.sha256(raw).hexdigest(),
                          'viewSHA': hashlib.sha256(view).hexdigest(), 'transformSHA': policy_sha}))
    except (OSError, ValueError, TypeError):
        parser.exit(1, 'View refused: check source, policy and a new output path.\n')
