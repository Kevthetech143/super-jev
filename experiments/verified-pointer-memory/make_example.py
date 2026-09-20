"""Create a safe synthetic dataset for the public quickstart, not user-data approval."""
import hashlib
import json
import os
import sys
from pathlib import Path


def main():
    os.umask(0o077)
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source = destination / 'source.txt'
    text = 'The example project launch color is blue.'
    source.write_text(text)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = destination / 'manifest.json'
    manifest.write_text(json.dumps({
        'expectedPolicy': 'reviewed', 'descriptionsAffirmed': True,
        'sources': [{'id': 'launch', 'path': str(source), 'contentSHA': digest,
                     'description': 'Synthetic example project launch color.'}],
        'preparations': [{'sourceId': 'launch', 'contentSHA': digest, 'chunkIndex': 0,
                          'startLine': 1, 'endLine': 1, 'reviewedText': text,
                          'safeHeading': 'Synthetic example', 'policy': 'reviewed', 'status': 'reviewed'}],
    }, indent=2))
    (destination / 'registry.json').write_text(json.dumps({
        'version': 1, 'datasets': {'example': {
            'description': 'One synthetic launch-color record; no personal data.',
            'manifestPath': str(manifest), 'manifestSHA256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'originals': [{'path': str(source), 'sha256': digest}],
        }},
    }, indent=2))
    (destination / 'config.json').write_text(json.dumps({'db': 'memory.sqlite', 'registry': 'registry.json'}, indent=2))
    (destination / 'register.json').write_text(json.dumps({'action': 'register', 'pointer': 'demo', 'dataset': 'example', 'principals': ['local']}))
    (destination / 'question.json').write_text(json.dumps({'pointer': 'demo', 'question': 'What is the example project launch color?', 'principal': 'local'}))
    print(json.dumps({'status': 'created', 'directory': str(destination), 'nextAction': 'register-then-search'}))


if __name__ == '__main__':
    main()
