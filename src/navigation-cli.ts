#!/usr/bin/env node
import { Jev } from './jev.ts';
import { navigate, NavigationError, providerFailureReason } from './enhance/navigation.ts';

async function stdin(): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const data = Buffer.from(chunk);
    size += data.length;
    if (size > 1024 * 1024) throw new NavigationError('Navigation input is too large');
    chunks.push(data);
  }
  const body = Buffer.concat(chunks, size);
  return body.toString('utf8');
}

async function main(): Promise<void> {
  // Navigation has exactly one provider evaluation per explored frontier.
  // Reject Jev's optional sampling/pin modes instead of silently changing the
  // call count or enabling its pin retry path for this command.
  if ((process.env.SUPERJEV_JUDGE_RUNS && process.env.SUPERJEV_JUDGE_RUNS !== '1') || process.env.SUPERJEV_JUDGE_TEMPERATURE || process.env.SUPERJEV_JUDGE_SEED) throw new NavigationError('Navigation requires one unpinned Jev run');
  let input: unknown;
  try { input = JSON.parse(await stdin()); }
  catch (error) { throw error instanceof NavigationError ? error : new NavigationError('Navigation input is not valid JSON'); }
  if (!input || typeof input !== 'object') throw new NavigationError('Navigation input is invalid');
  const value = input as { question?: unknown; catalog?: unknown; limits?: unknown };
  if (Object.keys(value).some(key => !['question', 'catalog', 'limits'].includes(key)) || (value.limits !== undefined && (!value.limits || typeof value.limits !== 'object' || Array.isArray(value.limits) || Object.keys(value.limits).some(key => !['beamWidth', 'maxRounds', 'maxResults'].includes(key))))) throw new NavigationError('Navigation input is invalid');
  // SUPERJEV_NAV_TIMEOUT_MS raises the per-navigate provider timeout above navigate()'s
  // 5_000ms default (that default was too short for a busy provider under concurrent
  // navigate calls, surfacing as "Navigation provider timed out"). navigate() itself
  // still rejects anything outside [1, 120_000], so an out-of-range or non-numeric
  // value here just falls through to that default rather than being silently clamped.
  const envTimeout = process.env.SUPERJEV_NAV_TIMEOUT_MS ? Number(process.env.SUPERJEV_NAV_TIMEOUT_MS) : undefined;
  const timeoutMs = Number.isInteger(envTimeout) ? envTimeout : 15_000;
  const result = await navigate(value.catalog, value.question, { ...(value.limits as object | undefined), timeoutMs, transport: new Jev() });
  process.stdout.write(JSON.stringify(result) + '\n');
}

main().catch(error => { process.stderr.write(`${error instanceof NavigationError ? error.message : `Navigation failed: ${providerFailureReason(error)}`}\n`); process.exitCode = 2; });
