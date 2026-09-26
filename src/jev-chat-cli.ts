#!/usr/bin/env node
// `superjev` -- a terminal chat over the super-jev cache-first lookup harness.
// Launch screen, first-run setup (API key + principal + folders), a chat loop
// that hits skills/super-jev/ask.py first (instant, zero-API-call replies on
// a cache hit) and shows ask.py's own report on a miss -- no live Jev call,
// since a single-question evaluation can't answer anything.
import { intro, outro, spinner, text, password, confirm, note, log, isCancel, cancel } from '@clack/prompts';
import pc from 'picocolors';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  loadConfig, saveConfig, configPath, hasApiKey,
  cannedReply, parseSlashCommand, askLookupArgs, isKnownSlashCommand,
  parseMissCandidate, formatMissCandidateReply, parseMissReport,
  parseSetupMissing, parseErrorReport, parseAnyPointers,
  MISS_LINE, HELP_TEXT,
  type SuperJevConfig,
} from './jev-chat-config.ts';

const VERSION = '0.1.0';
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..');
const ASK_PY = join(REPO_ROOT, 'skills', 'super-jev', 'ask.py');
const DISPATCH_PY = join(REPO_ROOT, 'skills', 'super-jev', 'dispatch.py');

const BANNER = String.raw`
   ____                       ____
  / ___| _   _ _ __   ___ _ __|  _ \ ___ __   __
  \___ \| | | | '_ \ / _ \ '__| | | |/ _ \\ \ / /
   ___) | |_| | |_) |  __/ |  | |_| |  __/ \ V /
  |____/ \__,_| .__/ \___|_|  |____/ \___|  \_/
              |_|
`;

function printBanner(model: string) {
  console.log(pc.cyan(BANNER));
  console.log(pc.bold('  Super Jev') + pc.dim(` v${VERSION}`) + `  -- model: ${pc.green(model)}`);
  console.log(pc.dim('  Tips: ask anything. /help for commands. /quit to leave.'));
  console.log();
}

function runAskPy(args: string[]): { code: number; stdout: string; stderr: string } {
  const result = spawnSync('python3', [ASK_PY, ...args], { encoding: 'utf8' });
  if (result.error) {
    return { code: 1, stdout: '', stderr: `no network / python3 unavailable: ${result.error.message}` };
  }
  return { code: result.status ?? 1, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

function parseHit(stdout: string): { answer: string; topFile: string } | null {
  if (!stdout.startsWith('CACHE HIT')) return null;
  const lines = stdout.split('\n');
  const answerLine = lines.find((l) => l.startsWith('answer:'));
  const evidenceLine = lines.find((l) => l.trim().startsWith('evidence:'));
  const answer = answerLine ? answerLine.slice('answer:'.length).trim() : '';
  const topFile = evidenceLine ? evidenceLine.trim().slice('evidence:'.length).trim().split('|')[0].trim() : 'unknown';
  return { answer, topFile };
}

/** Cheap, local, no-network check (panel action) for whether a principal has
 * any connected pointers -- run right after /setup so a typo'd or wrong-case
 * principal (pointers are matched exact-case) is caught immediately instead
 * of silently missing on every question. Returns null (skip the warning) if
 * the check itself fails for any reason -- this is a nice-to-have, never a
 * blocker on setup completing. */
function checkPrincipalHasConnections(principal: string): boolean | null {
  const result = spawnSync('python3', [DISPATCH_PY, 'memory', '--principal', principal], {
    encoding: 'utf8', timeout: 5000,
  });
  if (result.error || result.status !== 0 || !result.stdout) return null;
  return parseAnyPointers(result.stdout);
}

async function runSetup(existing: SuperJevConfig): Promise<SuperJevConfig> {
  note('First-time setup. Your API key is stored locally, never printed or logged.', 'Setup');
  const key = await password({
    message: 'TypeSafe API key (hidden, press enter to keep existing)',
    validate: () => undefined,
  });
  if (isCancel(key)) { cancel('Setup cancelled.'); process.exit(1); }

  const principal = await text({
    message: 'Principal name (who Super Jev looks things up for)',
    placeholder: existing.principal || 'me',
    defaultValue: existing.principal || 'me',
  });
  if (isCancel(principal)) { cancel('Setup cancelled.'); process.exit(1); }

  const foldersRaw = await text({
    message: 'Folders to search (comma-separated paths, optional)',
    placeholder: (existing.folders || []).join(', '),
    defaultValue: (existing.folders || []).join(', '),
  });
  if (isCancel(foldersRaw)) { cancel('Setup cancelled.'); process.exit(1); }

  const folders = String(foldersRaw).split(',').map((s) => s.trim()).filter(Boolean);
  // Pointers are matched exact-case, so a principal saved as "Primary" never
  // matches connections made under "primary" -- normalize at the one place
  // it's typed in, rather than leaving every future lookup to miss silently.
  const next: SuperJevConfig = {
    typesafeApiKey: (typeof key === 'string' && key.length > 0) ? key : existing.typesafeApiKey,
    principal: String(principal || existing.principal || 'me').trim().toLowerCase(),
    folders: folders.length ? folders : existing.folders,
  };
  saveConfig(next);
  log.success(`Saved config to ${configPath()} (0600, key never echoed).`);

  const connected = checkPrincipalHasConnections(next.principal!);
  if (connected === false) {
    log.warn(`No connected pointers found yet for principal '${next.principal}'. Lookups will miss until you connect a folder (see skills/super-jev/references/connectors.md), or the data was connected under a different principal name.`);
  }
  return next;
}

async function handleQuestion(question: string, principal: string) {
  const s = spinner();
  s.start('Super Jev is thinking');
  const hit = runAskPy(askLookupArgs(principal, question));
  const parsed = parseHit(hit.stdout);
  if (parsed) {
    s.stop('Found it.');
    console.log(pc.bold('Top file: ') + parsed.topFile);
    console.log(pc.bold('Answer: ') + (parsed.answer || '(no answer text)'));
    return;
  }

  // Setup problem, not a miss: this principal has no connected pointers at
  // all yet. Show ask.py's own message and the exact connect command instead
  // of the generic "I didn't have this" -- that line is misleading when the
  // real issue is nothing is connected (or the principal name doesn't match
  // what it was connected under -- pointers are matched exact-case).
  const setupMissing = parseSetupMissing(hit.stdout);
  if (setupMissing) {
    s.stop('Not set up for this yet.');
    console.log();
    console.log(setupMissing.join('\n'));
    return;
  }

  // Miss, but ask.py still ranked candidate files (no approved answer yet):
  // a real short reply -- top file + one-line why -- never the raw score/
  // pointer line, and no live call needed since we already have a lead.
  const candidate = parseMissCandidate(hit.stdout);
  if (candidate) {
    s.stop('No saved answer yet.');
    console.log(pc.bold('Super Jev: ') + formatMissCandidateReply(candidate));
    return;
  }

  // True miss (no candidates at all, or ask.py/network unavailable): no live
  // call -- a single-question evaluation can't answer anything and would
  // just cost money. Show ask.py's own miss report (what was searched, and
  // the next step to take) instead.
  s.stop('No saved answer yet.');
  const missReport = parseMissReport(hit.stdout);
  console.log();
  if (missReport) console.log(missReport.join('\n'));

  // If every pointer actually errored out (auth failure, network, etc.) this
  // is not an honest "we don't have it" -- surface the real reason instead of
  // a silent, unexplained miss.
  const errorReport = parseErrorReport(hit.stdout);
  if (errorReport) console.log(pc.red(errorReport.join('\n')));

  console.log(pc.yellow(MISS_LINE));
  if (hit.stderr) console.log(pc.dim(hit.stderr.trim().split('\n').slice(0, 3).join('\n')));
}

async function main() {
  console.clear?.();
  let config = loadConfig();
  printBanner(config.principal ? 'super-jev cache + live' : 'not configured');

  // Non-interactive / non-TTY environments (CI, pipes): don't prompt at all.
  if (!process.stdin.isTTY) {
    console.log('No TTY detected. Run superjev in a terminal to set up and chat.');
    return;
  }

  if (!hasApiKey(config) || !config.principal) {
    config = await runSetup(config);
  }

  intro(pc.cyan('Super Jev chat'));
  const principal = config.principal || 'me';

  for (;;) {
    const input = await text({ message: 'you' });
    if (isCancel(input) || input === undefined) { outro('Bye.'); return; }
    const line = String(input).trim();
    if (!line) continue;

    const slash = parseSlashCommand(line);
    if (slash) {
      if (!isKnownSlashCommand(slash.command)) {
        console.log(pc.red(`Unknown command: /${slash.command}. Try /help.`));
        continue;
      }
      if (slash.command === 'help') { console.log(HELP_TEXT); continue; }
      if (slash.command === 'quit') { outro('Bye.'); return; }
      if (slash.command === 'setup') { config = await runSetup(config); continue; }
      if (slash.command === 'folders') {
        if (slash.args.length === 0) {
          console.log('Folders: ' + ((config.folders && config.folders.length) ? config.folders.join(', ') : '(none set -- run /setup)'));
        } else {
          config = { ...config, folders: slash.args.join(' ').split(',').map((s) => s.trim()).filter(Boolean) };
          saveConfig(config);
          console.log(pc.green('Folders updated.'));
        }
        continue;
      }
      continue;
    }

    const canned = cannedReply(line);
    if (canned) { console.log(pc.bold('Super Jev: ') + canned); continue; }

    await handleQuestion(line, principal);
  }
}

main().catch((err) => {
  console.error(pc.red('superjev: ' + (err && err.message ? err.message : String(err))));
  process.exitCode = 1;
});
