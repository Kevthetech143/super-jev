"""Local-only experimental control panel and input/output interface."""
import argparse
import json
import math
import os
import sqlite3
import shlex
import sys
import subprocess
from pathlib import Path
from service import Service

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'skills' / 'super-jev'))
from prepare_bulk import has_secret, payload_has_secret  # noqa: E402

NEXT = {
    'ready': 'verify-evidence-then-approve',
    'verified-cache-hit': 'use-cited-answer',
    'preparation-required': 'review-and-refresh-preparation',
    'refresh-required': 'refresh-source-and-preparation',
    'unknown-pointer': 'register-reviewed-dataset',
    'access-denied': 'stop-access-denied',
    'no-match': 'record-unresolved',
    'candidates': 'inspect-source-files',
    'no-candidates': 'record-unresolved-navigation',
    'cache-miss': 'navigate-or-search',
    'budget-exhausted': 'inspect-candidates-and-unexplored-trace',
    'refused': 'record-refusal',
    'error': 'record-error',
    'pointer-changed': 'resubmit-after-review',
    'registered': 'search', 'removed': 'done', 'saved': 'continue', 'ok': 'choose-action',
}
DEFAULTS = {'cacheTtlSeconds': 86400, 'reviewTtlSeconds': 600, 'providerTimeoutSeconds': 120}
ACTIONS = {
    'describe': [], 'panel': ['principal'],
    'connect': ['pointer', 'sources', 'principals'],
    'register': ['pointer', 'dataset', 'principals'], 'remove': ['pointer'],
    'search': ['pointer', 'question', 'principal'],
    'navigate': ['pointer', 'question', 'principal'],
    'navigate-many': ['pointers', 'question', 'principal'],
    'cached': ['principal', 'question'],
    'forget': ['principal', 'question'],
    'approve': ['ticket', 'principal', 'approved', 'answer', 'evidence'],
    'assist': ['attemptId', 'principal', 'reason', 'references'],
    'attempt': ['attemptId', 'principal'],
    'open': ['pointer', 'question', 'principal'],
    'sources': ['pointer', 'principal'],
}


def describe():
    """Return supported options without needing an account or configuration."""
    return {
        'status': 'ok', 'stage': 'local-experiment', 'actions': ACTIONS,
        'connectorGuide': 'skills/super-jev/references/connectors.md',
        'setupHelp': {'command': 'dispatch.py help --question QUESTION',
                      'topicCommand': 'dispatch.py help --topic TOPIC',
                      'kind': 'bundled setup help, not user-data answer memory',
                      'needsDataOrProviderKey': False},
        'connectorStatusMeaning': 'Supported workflows, not connection health; inspect configured roots and pointer status for readiness.',
        'sourceConnectors': {
            'Skills': {'availability': 'configured-local-roots', 'tool': 'skills'},
            'Brain': {'availability': 'reviewed-local-files', 'tools': ['find', 'memory']},
            'Documents': {'availability': 'reviewed-local-files', 'tools': ['find', 'memory']},
            'Repo': {'availability': 'reviewed-local-snapshot', 'tools': ['find', 'memory'],
                     'automaticSync': False},
            'Database': {'availability': 'proposed', 'directIngestion': False},
            'Website': {'availability': 'proposed', 'directIngestion': False},
        },
        'agentSetupWorkflows': {
            'mode': 'agent-guided; these are not API actions',
            'when': 'New connection or setup/refresh problem only; existing connections use their known tool/pointer directly.',
            'options': ['connect-existing-data', 'start-new-collection',
                        'define-custom-connector', 'refresh-reviewed-connection'],
            'customNames': True,
            'existingData': 'Preserve originals; optimize a reviewed searchable view.',
            'newData': 'Optional INDEX.md and records/ with stable IDs, descriptions and paths; skills retain SKILL.md metadata.',
            'onboarding': 'memory connect prepares and registers explicit local UTF-8 files after agent source review; no manual chunking required.',
            'refresh': 'Manual preparation and re-registration; no automatic fetch/watch service.',
            'extension': 'Reuse reviewed datasets or implement a checked trusted retrievalCommand adapter; no automatic connector plugin registry.',
        },
        'connectQuickStart': {'preview': 'memory --connect my-records --file /absolute/path/to/record.md --principal YOUR_AGENT_NAME',
                              'confirmation': 'Review the files, then run the exact confirmCommand returned by the preview.',
                              'scope': 'Shortcut uses one principal and dataset equal to pointer; use JSON for shared/existing scopes.'},
        'connectOptions': {'sources': 'Explicit local UTF-8 files: path, optional description/id, reviewed sha256',
                           'structure': 'flat-files (default) or folder-tree; optional per-source navigationPath in JSON',
                           'reviewed': 'true only after permission/content review for provider processing',
                           'replace': 'true to refresh the same dataset and principal scope; invalidates its cached answers',
                           'limitations': 'No recursive folders, PDF extraction, URL/DB fetching or automatic synchronization'},
        'settings': DEFAULTS, 'optionalSearchFields': ['context', 'freshness'],
        'optionalNavigateFields': ['limits'],
        'navigationLimits': {'beamWidth': {'default': 3, 'min': 1, 'max': 5},
                             'maxRounds': {'default': 6, 'min': 1, 'max': 10},
                             'maxResults': {'default': 3, 'min': 1, 'max': 10}},
        'optionalConfigDefaults': {'allowAgentAssist': False},
        'assistLimits': {'maxPreparations': 20, 'maxReviewedCharacters': 60000},
        'freshnessPolicies': {
            'default': {'mode': 'snapshot'},
            'current': {'mode': 'current', 'maxAgeSeconds': 'positive finite seconds'},
            'limitation': 'current requires dataset.checkedAt from a trusted upstream whole-scope check; it does not establish domain truth.',
        },
        'contextLimitation': 'Context scopes the exact cache only; it is not sent to the retrieval provider.',
        'principalScope': 'Principal labels are trusted-local scope labels, not authentication.',
        'pointerLifecycle': {
            'register': 'Replaces the pointer generation and invalidates its answers and pending tickets.',
            'remove': 'Removes the pointer, cache and pending tickets only; it never removes originals.',
        },
        'requiredConfig': ['db', 'registry'],
        'optionalConfig': ['retrievalCommand', 'navigationCommand',
                           'allowAgentAssist', *DEFAULTS],
        'resultActions': NEXT,
        'requirements': ['Python 3.10+', 'Node 24+ for bundled retrieval',
                         'Reviewed local dataset', 'TYPESAFE_API_KEY for live Jev calls'],
        'supported': ['Persistent dataset pointers', 'Exact verified-answer reuse',
                      'Source freshness checks', 'Explicit review tickets'],
        'notSupported': ['Untrusted multi-user hosting', 'Automatic private-data approval',
                         'Semantic cache matching', 'Automatic source fetching or freshness watching',
                         'Autonomous background queue',
                         'Arbitrary-format ingestion or recursive crawling', 'Original-source editing or deletion'],
    }


def add_hints(result, config=None):
    """Attach small deterministic operator guidance to actionable results."""
    status = result.get('status')
    hints = []
    needs_start = status in ('unknown-pointer', 'preparation-required') or (
        status == 'ok' and result.get('pointers') == [])
    if needs_start:
        if result.get('reason') == 'review-required':
            message = 'Your files were found. Review their contents and existing permission for Jev, then confirm the returned hashes with reviewed:true. The harness will build the searchable copy.'
        elif result.get('hint'):
            message = result['hint']
        else:
            message = "Let's connect your records so Super Jev can search them. Choose the original files for this person or project; the agent can review them and run connect to build the searchable copy. If these files were connected before, refresh that connection instead."
        result.setdefault('message', message)
        result.setdefault('gettingStarted', {
            'previewCommand': 'python3 dispatch.py memory --connect my-records --file /absolute/path/to/record.md --principal YOUR_AGENT_NAME',
            'commandContext': 'Run from the installed skill directory; replace the example path and principal. Review files before running the returned confirmCommand.',
            'guide': 'references/connectors.md#connect-local-files-paths-to-searchable-passages',
            'steps': ['Choose the authorized original text files for this person/project. If you have no records yet, the agent can help create them from your supplied facts.',
                      'Submit a connect request for a local preview, review the files and permission, then confirm the returned sha256 values with reviewed:true.',
                      'Once registered, search using the returned memory pointer. Reuse that connection next time; do not repeat onboarding.'],
            'requestTemplate': {'action': 'connect', 'pointer': 'CHOSEN_SCOPE_NAME',
                                'principals': ['YOUR_AGENT_NAME'],
                                'sources': [{'path': '/absolute/path/to/original-record.md'}]},
            'command': 'python3 <skill-directory>/dispatch.py memory --input CONNECT.json',
        })
    if status == 'no-match' and config and config.get('allowAgentAssist'):
        hints.append({'code': 'inspect-sources',
                      'message': 'Review registered sources for relevant line ranges.',
                      'action': 'sources'})
    elif status == 'ready':
        hints.append({'code': 'review-evidence',
                      'message': 'Check that the passages fully support the answer, then approve their evidence IDs.',
                      'action': 'approve'})
    elif status == 'preparation-required':
        hints.append({'code': 'refresh-preparation',
                      'message': 'Use the memory connect JSON action with the authorized original text-file paths to prepare this scope. Review the source hashes, then confirm reviewed:true; use replace:true only to refresh the same connector. See references/connectors.md.',
                      'action': 'connect'})
    elif status == 'refresh-required':
        hints.append({'code': 'refresh-upstream',
                      'message': 'Obtain current source material and run the trusted whole-scope freshness check; refresh preparation and re-register if changed. Registration alone does not sync data.',
                      'action': 'search'})
    elif status == 'unknown-pointer':
        hints.append({'code': 'connect-records',
                      'message': 'Use gettingStarted to connect your authorized original files, or select an existing pointer from your memory panel.',
                      'action': 'connect'})
    elif (status == 'error' and result.get('reason') == 'agent assist is disabled'):
        hints.append({'code': 'assist-disabled',
                      'message': 'An operator can enable assistance with allowAgentAssist.',
                      'action': 'panel'})
    if status in ('ready', 'verified-cache-hit') and result.get('freshness', {}).get('mode') == 'snapshot':
        hints.append({'code': 'snapshot-freshness',
                      'message': 'Based on the registered snapshot, not a live sync. For current-state questions, refresh the source and preparation or request current-mode freshness checks.',
                      'action': 'search'})
    if status == 'ok' and result.get('pointers'):
        missing = sum(p.get('missingSourceDescriptions', 0)
                      for p in result['pointers'])
        if missing:
            hints.append({'code': 'source-descriptions-missing',
                          'message': f'{missing} registered sources have no description.',
                          'action': 'register'})
    if hints:
        result['hints'] = result.get('hints', []) + hints
    return result


def load_config(path):
    """Resolve deployment-local paths and reject unsupported configuration."""
    location = Path(path).resolve()
    config = json.loads(location.read_text())
    if not isinstance(config, dict):
        raise ValueError('Config must be an object.')
    unknown = set(config) - {'db', 'registry', 'retrievalCommand', 'navigationCommand',
                             'allowAgentAssist', *DEFAULTS}
    if unknown:
        raise ValueError('Unsupported configuration setting.')
    for name in ('db', 'registry'):
        p = Path(config[name]).expanduser()
        config[name] = str(p if p.is_absolute() else location.parent / p)
    for name, default in DEFAULTS.items():
        value = config.setdefault(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Time settings must be positive finite seconds.')
    command = config.setdefault('retrievalCommand', ['node', str(Path(__file__).with_name('retrieve.ts').resolve())])
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise ValueError('retrievalCommand must be an administrator-provided argument array.')
    navigation = config.setdefault('navigationCommand', ['node', str(Path(__file__).parents[2] / 'src' / 'navigation-cli.ts')])
    if not isinstance(navigation, list) or not navigation or any(not isinstance(x, str) or not x for x in navigation):
        raise ValueError('navigationCommand must be an administrator-provided argument array.')
    assist = config.setdefault('allowAgentAssist', False)
    if not isinstance(assist, bool):
        raise ValueError('allowAgentAssist must be a boolean.')
    return config


def run(request, config):
    """Execute one explicit action; no implicit fallback or approval."""
    if payload_has_secret(request):
        raise ValueError('Request contains a secret; not sent.')
    def retrieve(dataset, question):
        payload = {'registry': config['registry'], 'dataset': dataset, 'question': question}
        try:
            process = subprocess.run(
                config['retrievalCommand'], input=json.dumps(payload),
                capture_output=True, text=True, timeout=config['providerTimeoutSeconds'],
            )
            if process.returncode:
                return {'status': 'error', 'reason': 'Retrieval command failed.'}
            result = json.loads(process.stdout)
            if not isinstance(result, dict) or result.get('status') not in NEXT:
                raise ValueError('Invalid retrieval output.')
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {'status': 'error', 'reason': 'Retrieval failed or returned invalid JSON.'}

    def navigate_provider(question, catalog, limits):
        payload = {'question': question, 'catalog': catalog}
        if limits is not None:
            payload['limits'] = limits
        try:
            process = subprocess.run(
                config['navigationCommand'], input=json.dumps(payload), capture_output=True,
                text=True, timeout=config['providerTimeoutSeconds'])
            if process.returncode:
                # navigation-cli prints one content-free cause line (HTTP code, missing key, network)
                cause = (process.stderr or '').strip().splitlines()[-1:] or ['Navigation failed']
                return {'status': 'error', 'reason': cause[0][:200]}
            return json.loads(process.stdout)
        except subprocess.TimeoutExpired:
            return {'status': 'error', 'reason': 'Navigation timed out.'}
        except (OSError, ValueError):
            return {'status': 'error', 'reason': 'Navigation failed or returned invalid JSON.'}

    def navigate_many_provider(question, catalogs, limits):
        batch = [{'question': question, 'catalog': catalog, **({'limits': limits} if limits is not None else {})}
                 for catalog in catalogs]
        try:
            process = subprocess.run(
                config['navigationCommand'], input=json.dumps({'batch': batch}), capture_output=True,
                text=True, timeout=config['providerTimeoutSeconds'])
            if process.returncode:
                cause = (process.stderr or '').strip().splitlines()[-1:] or ['Navigation failed']
                return {'status': 'error', 'reason': cause[0][:200]}
            out = json.loads(process.stdout)
            return out.get('results') if isinstance(out, dict) else None
        except subprocess.TimeoutExpired:
            return {'status': 'error', 'reason': 'Navigation timed out.'}
        except (OSError, ValueError):
            return {'status': 'error', 'reason': 'Navigation failed or returned invalid JSON.'}

    if request.get('action') == 'connect':
        from path_connect import connect
        return connect(request, config)

    service = Service(config['db'], config['registry'], retrieve,
                      navigate_provider=navigate_provider,
                      navigate_many_provider=navigate_many_provider,
                      cache_ttl_seconds=config['cacheTtlSeconds'],
                      review_ttl_seconds=config['reviewTtlSeconds'],
                      allow_agent_assist=config['allowAgentAssist'])
    action = request.get('action', 'search')
    if action not in ACTIONS:
        raise ValueError('Unknown action; use --describe.')
    for field in ACTIONS[action]:
        if field not in request:
            raise ValueError(f'Missing required field: {field}')
    if action == 'panel':
        registry = json.loads(Path(config['registry']).read_text())
        with service.connect() as connection:
            rows = connection.execute('SELECT name, body FROM pointers ORDER BY name').fetchall()
        pointers = []
        for name, body in rows:
            binding = json.loads(body)
            _, error = service.pointer(name, request['principal'])
            if error and error['status'] == 'access-denied':
                continue
            entry = registry['datasets'].get(binding['dataset'], {})
            pointers.append({
                'pointer': name, 'dataset': binding['dataset'],
                'generation': binding['generation'],
                'checkedAt': entry.get('checkedAt'),
                'datasetScope': entry.get('scope', entry.get('description', '')),
                'snapshotStatus': error['status'] if error else 'available',
                'status': error['status'] if error else 'available',
                'structure': entry.get('structure',
                                       binding.get('snapshot', {}).get('entry', {}).get('structure',
                                                   'flat-files')),
                'missingSourceDescriptions': sum(
                    1 for source in binding.get('snapshot', {}).get('sources', [])
                    if not isinstance(source.get('description'), str)
                    or not source['description'].strip()),
            })
        return {**describe(), 'settings': {k: config[k] for k in DEFAULTS},
                'agentAssistEnabled': config['allowAgentAssist'],
                'storagePaths': {'db': config['db'], 'registry': config['registry']},
                'pointers': pointers,
                'datasets': [{'name': name, 'description': entry.get('description', '')}
                             for name, entry in registry['datasets'].items()]}
    if action == 'search':
        if 'freshness' in request and request['freshness'] is None:
            raise ValueError('freshness must be an object when supplied.')
        return service.search(request['pointer'], request['question'], request['principal'],
                              request.get('context', ''), freshness=request.get('freshness'))
    if action == 'navigate':
        return service.navigate(request['pointer'], request['principal'],
                                request['question'], request.get('limits'))
    if action == 'navigate-many':
        return service.navigate_many(request['pointers'], request['principal'],
                                     request['question'], request.get('limits'))
    if action == 'cached':
        return service.cached(request['principal'], request['question'],
                              request.get('pointer'), request.get('context', ''))
    if action == 'forget':
        return service.forget(request['principal'], request['question'], request.get('context', ''))
    if action == 'register':
        service.register(request['pointer'], request['dataset'], request['principals'])
        return {'status': 'registered'}
    if action == 'remove':
        service.remove(request['pointer'])
        return {'status': 'removed'}
    if action == 'approve':
        service.approve(request['ticket'], request['principal'], request['answer'], request['evidence'], approved=request['approved'] is True)
        return {'status': 'saved'}
    if action == 'assist':
        return service.assist(request['attemptId'], request['principal'],
                              request['reason'], request['references'])
    if action == 'open':
        return service.open_attempt(request['pointer'], request['question'],
                                    request['principal'], request.get('context', ''))
    if action == 'attempt':
        return service.attempt(request['attemptId'], request['principal'])
    if action == 'sources':
        return service.sources(request['pointer'], request['principal'],
                               request.get('offset', 0), request.get('limit', 25))
    return describe()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--describe', action='store_true', help='Show actions, settings and limits without setup.')
    parser.add_argument('--config')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--input', help='JSON action file; omit for the control panel.')
    mode.add_argument('--connect', metavar='NAME', help='Preview or connect a named set of local text files.')
    parser.add_argument('--file', action='append', default=[], metavar='PATH', help='File to preview; repeat for more files.')
    parser.add_argument('--reviewed-file', action='append', nargs=2, default=[], metavar=('PATH', 'SHA256'), help='Confirm reviewed file bytes and permission for provider processing; repeat for more files.')
    parser.add_argument('--replace', action='store_true', help='Refresh an existing connector with the same scope.')
    parser.add_argument('--structure', choices=('flat-files', 'folder-tree'), help='Navigation structure for this connector.')
    parser.add_argument('--navigation-sha', help='Confirm the exact reviewed navigation catalog.')
    parser.add_argument('--principal', default='local', help='Local scope for the control panel, not authentication.')
    args = parser.parse_args()
    config = None
    try:
        if (args.file or args.reviewed_file or args.replace or args.structure or args.navigation_sha) and not args.connect:
            raise ValueError('--file, --reviewed-file, --replace, --structure and --navigation-sha require --connect.')
        if args.file and args.reviewed_file:
            raise ValueError('Use --file for preview OR --reviewed-file for confirmation, not both.')
        if args.describe:
            result = describe()
        else:
            if not args.config:
                raise ValueError('Supply --config, or use --describe.')
            if args.connect:
                request = {'action': 'connect', 'pointer': args.connect, 'principals': [args.principal],
                           'sources': ([{'path': path, 'sha256': sha} for path, sha in args.reviewed_file]
                                       if args.reviewed_file else [{'path': path} for path in args.file]),
                           'reviewed': bool(args.reviewed_file), 'replace': args.replace,
                           **({'structure': args.structure} if args.structure else {}),
                           **({'navigationSHA': args.navigation_sha} if args.navigation_sha else {})}
            else:
                request = json.loads(Path(args.input).read_text()) if args.input else {'action': 'panel', 'principal': args.principal}
            if not isinstance(request, dict):
                raise ValueError('Input must be a JSON object.')
            config = load_config(args.config)
            result = run(request, config)
            if args.connect and result.get('reason') == 'review-required':
                confirm = [sys.executable, str(Path(__file__).resolve()), '--config', str(Path(args.config).resolve()),
                           '--connect', args.connect, '--principal', args.principal]
                for source in result['sources']:
                    confirm.extend(['--reviewed-file', source['path'], source['sha256']])
                if args.replace:
                    confirm.append('--replace')
                if args.structure:
                    confirm.extend(['--structure', result['structure']])
                if args.structure or result.get('navigationSHA'):
                    confirm.extend(['--navigation-sha', result['navigationSHA']])
                result['confirmCommand'] = shlex.join(confirm)
                result['confirmWhen'] = 'Run only after reviewing these files and existing permission for their full text to be processed by the configured provider.'
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        # Do not expose provider stderr, credentials, source passages or cache bodies.
        result = {'status': 'error', 'reason': str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__}
    add_hints(result, config)
    result.setdefault('nextAction', NEXT.get(result['status'], 'record-unresolved'))
    if 'message' in result:
        result = {'message': result.pop('message'), **result}
    print(json.dumps(result))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
