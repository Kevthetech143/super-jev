#!/usr/bin/env node
import { Jev } from './jev.ts';
import { navigate, NavigationError, providerFailureReason } from './enhance/navigation.ts';
import { BatchingEvaluator } from './enhance/coalesce.ts';

// A batch carries every pointer's catalog, so it may be larger than one navigate.
const MAX_INPUT_BYTES = 8 * 1024 * 1024;
const MAX_BATCH = 200;

async function stdin(): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    const data = Buffer.from(chunk);
    size += data.length;
    if (size > MAX_INPUT_BYTES) throw new NavigationError('Navigation input is too large');
    chunks.push(data);
  }
  const body = Buffer.concat(chunks, size);
  return body.toString('utf8');
}

function checkedInput(input: unknown): { question?: unknown; catalog?: unknown; limits?: unknown } {
  if (!input || typeof input !== 'object') throw new NavigationError('Navigation input is invalid');
  const value = input as { question?: unknown; catalog?: unknown; limits?: unknown };
  if (Object.keys(value).some(key => !['question', 'catalog', 'limits'].includes(key)) || (value.limits !== undefined && (!value.limits || typeof value.limits !== 'object' || Array.isArray(value.limits) || Object.keys(value.limits).some(key => !['beamWidth', 'maxRounds', 'maxResults'].includes(key))))) throw new NavigationError('Navigation input is invalid');
  return value;
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
  // SUPERJEV_NAV_TIMEOUT_MS raises the per-navigate provider timeout above navigate()'s
  // 5_000ms default (that default was too short for a busy provider under concurrent
  // navigate calls, surfacing as "Navigation provider timed out"). navigate() itself
  // rejects anything outside [1, 120_000], so an out-of-range or non-numeric value
  // here falls back to 15_000 instead of failing every navigate call.
  const envTimeout = process.env.SUPERJEV_NAV_TIMEOUT_MS ? Number(process.env.SUPERJEV_NAV_TIMEOUT_MS) : undefined;
  const timeoutMs = Number.isInteger(envTimeout) && (envTimeout as number) >= 1 && (envTimeout as number) <= 120_000 ? envTimeout : 15_000;
  const batch = (input as { batch?: unknown }).batch;
  if (batch !== undefined && (Object.keys(input).length !== 1 || !Array.isArray(batch) || !batch.length || batch.length > MAX_BATCH)) throw new NavigationError('Navigation input is invalid');
  const single = batch === undefined ? checkedInput(input) : undefined;
  const transport = new BatchingEvaluator(new Jev());
  if (Array.isArray(batch)) {
    // {"batch": [navigate input, ...]}: every navigation runs at once and their Jev
    // questions share calls; one navigation's failure is its own error row.
    const results = await Promise.all(batch.map(async item => {
      try {
        const value = checkedInput(item);
        return await navigate(value.catalog, value.question, { ...(value.limits as object | undefined), timeoutMs, transport });
      } catch (error) {
        return { status: 'error', reason: error instanceof NavigationError ? error.message : `Navigation failed: ${providerFailureReason(error)}` };
      }
    }));
    process.stdout.write(JSON.stringify({ results, calls: transport.calls }) + '\n');
    return;
  }
  const result = await navigate(single!.catalog, single!.question, { ...(single!.limits as object | undefined), timeoutMs, transport });
  process.stdout.write(JSON.stringify(result) + '\n');
}

main().catch(error => { process.stderr.write(`${error instanceof NavigationError ? error.message : `Navigation failed: ${providerFailureReason(error)}`}\n`); process.exitCode = 2; });
