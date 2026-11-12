// `superjev connect <folder> [--name X] [--me P]` and `superjev ask "question" [--me P]`:
// a thin wrapper over prepare_bulk.py and ask.py. It builds the helper's argv (helperCall), runs it, prints one plain line.
import { spawnSync } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, join, resolve } from 'node:path';
import { helperCall, pointerName, type Session } from './jev-chat-config.ts';

export type DoorIO = { argv: string[]; env: NodeJS.ProcessEnv; skillDir: string; out: (s: string) => void; err: (s: string) => void };

export const isFrontDoor = (argv: string[]) => argv[0] === 'connect' || argv[0] === 'ask';

export function frontDoor({ argv, env, skillDir, out, err }: DoorIO): number {
  const [cmd, ...rest] = argv;
  let me = env.USER || 'me', name: string | undefined;
  const words: string[] = [];
  for (let i = 0; i < rest.length; i++) {
    if (rest[i] === '--me') me = rest[++i] ?? me;
    else if (rest[i] === '--name') name = rest[++i];
    else words.push(rest[i]);
  }
  const s: Session = { principal: me.toLowerCase().replace(/[^a-z0-9_-]/g, '') || 'me', skillDir, connected: new Set() };
  const run = (call: string[]) => spawnSync('python3', call, { encoding: 'utf8', env, maxBuffer: 1 << 26 });
  const json = (r: { stdout: string }) => { try { return JSON.parse(r.stdout.trim().split('\n').pop() ?? ''); } catch { return null; } };

  // first use: setup.py only works on a state folder that does not exist yet, so it runs before anything else touches it
  const state = env.SUPERJEV_STATE_DIR || join(homedir(), '.local/state/super-jev');
  if (!existsSync(join(state, '_memory', 'config.json'))) run([join(skillDir, 'setup.py')]);

  if (cmd === 'connect') {
    const folder = words[0] ? resolve(words[0]) : '';
    if (!folder || !statSync(folder, { throwIfNoEntry: false })?.isDirectory()) { err('Usage: superjev connect <folder> [--name X]   (that folder was not found)'); return 2; }
    const call = helperCall({ kind: 'connect', path: folder, dir: true, pointer: name ?? pointerName(folder) }, s).filter((a) => a !== '--refresh');
    const r = run(call), d = json(r);
    if (r.error) { err('Super Jev needs Python 3.10 or newer, and python3 was not found.'); return 1; }
    if (r.status === 0 && d && d.connected > 0) { out(`Connected ${d.connected} file${d.connected === 1 ? '' : 's'} from ${basename(folder)}. Ask me anything.`); return 0; }
    err(d?.refused?.why ? `Not connected: ${d.refused.why}` : d && d.connected === 0 ? `Nothing connected: no Markdown notes found in ${basename(folder)}.` : `Not connected. ${(r.stderr || r.stdout).trim().split('\n').pop()}`);
    return r.status || 1;
  }

  const q = words.join(' ').trim();
  if (!q) { err('Usage: superjev ask "your question"'); return 2; }
  const r = run(helperCall({ kind: 'ask', text: q }, s)), d = json(r);
  if (r.error) { err('Super Jev needs Python 3.10 or newer, and python3 was not found.'); return 1; }
  const top = d?.files?.[0];
  if (top) { out(`Top match: ${top.path}${top.text ? `\n${top.text}` : ''}`); return 0; }
  err(d ? `Nothing found.${d.why ? ' ' + d.why : ''}` : `Ask failed. ${(r.stderr || r.stdout).trim().split('\n').pop()}`);
  return r.status || 1;
}
