import { appendFile, mkdir, readFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Event, Journal } from './types.ts';

export class JsonlJournal implements Journal {
  path: string;
  constructor(path: string) { this.path = path; }
  async append(event: Event) {
    await mkdir(dirname(this.path), { recursive: true });
    await appendFile(this.path, JSON.stringify(event) + '\n', { mode: 0o600 });
  }
}
export async function readJournal(path: string): Promise<Event[]> {
  const lines = (await readFile(path, 'utf8')).trim().split('\n').filter(Boolean);
  return lines.map((line, index) => {
    try { return JSON.parse(line); } catch { throw new Error(`Invalid journal line ${index + 1}`); }
  });
}
