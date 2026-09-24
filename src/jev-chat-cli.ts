#!/usr/bin/env node
// `superjev` -- a terminal chat over the super-jev cache-first lookup harness.
// Launch screen, first-run setup (API key + principal + folders), a chat loop
// that hits skills/super-jev/ask.py first (instant, zero-API-call replies on
// a cache hit) and falls back to a live Jev call on a miss, caching the
// result with --add so the next ask of the same question is instant too.
import { intro, outro, spinner, text, password, confirm, note, log, isCancel, cancel } from '@clack/prompts';
import pc from 'picocolors';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  loadConfig, saveConfig, configPath, hasApiKey,
  cannedReply, parseSlashCommand, askLookupArgs, isKnownSlashCommand,
  parseMissCandidate, formatMissCandidateReply,
  MISS_LINE, HELP_TEXT,
  type SuperJevConfig,
} from './jev-chat-config.ts';

const VERSION = '0.1.0';
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..');
const ASK_PY = join(REPO_ROOT, 'skills', 'super-jev', 'ask.py');

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
  const next: SuperJevConfig = {
    typesafeApiKey: (typeof key === 'string' && key.length > 0) ? key : existing.typesafeApiKey,
    principal: String(principal || existing.principal || 'me'),
    folders: folders.length ? folders : existing.folders,
  };
  saveConfig(next);
  log.success(`Saved config to ${configPath()} (0600, key never echoed).`);
  return next;
}

async function handleQuestion(question: string, principal: string, apiKey?: string) {
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

  // Miss, but ask.py still ranked candidate files (no approved answer yet):
  // a real short reply -- top file + one-line why -- never the raw score/
  // pointer line, and no live call needed since we already have a lead.
  const candidate = parseMissCandidate(hit.stdout);
  if (candidate) {
    s.stop('No saved answer yet.');
    console.log(pc.bold('Super Jev: ') + formatMissCandidateReply(candidate));
    return;
  }

  // True miss (no candidates at all, or ask.py/network unavailable): try one
  // live call, then cache it.
  s.message('No cache hit, trying a live lookup');
  let liveAnswer: string | null = null;
  const key = apiKey || process.env.TYPESAFE_API_KEY;
  if (key) {
    try {
      const { Jev } = await import('./jev.ts');
      const jev = new Jev({ apiKey: key });
      const evaluation = await jev.evaluate(
        { model: 'jev-latest', question, answers: [{ id: 'a', text: question }] } as any,
        new AbortController().signal,
      );
      liveAnswer = JSON.stringify(evaluation).slice(0, 500);
    } catch (err) {
      liveAnswer = null;
    }
  }
  s.stop('Done.');

  if (liveAnswer) {
    // Not cached: this is a raw Jev evaluation (a grade), not an answer, and
    // writing it with --add would poison the approved-answer cache.
    console.log(pc.bold('Answer (live): ') + liveAnswer);
    return;
  }

  console.log();
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

    await handleQuestion(line, principal, config.typesafeApiKey);
  }
}

main().catch((err) => {
  console.error(pc.red('superjev: ' + (err && err.message ? err.message : String(err))));
  process.exitCode = 1;
});
