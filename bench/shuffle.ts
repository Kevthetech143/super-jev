/**
 * Deterministic record ordering for the measurement runner.
 *
 * Order matters here: the pilot's clearest finding was that answers moved when
 * the record order moved, so every condition states its order explicitly and
 * every shuffle is reproducible from its seed alone. No `Math.random` anywhere
 * in this bench.
 */

/** mulberry32. Small, fast, fully determined by the seed. Not cryptographic. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type OrderSpec = { kind: 'forward' } | { kind: 'reverse' } | { kind: 'shuffle'; seed: number };

export function describeOrder(order: OrderSpec): string {
  return order.kind === 'shuffle' ? `shuffled seed ${order.seed}` : order.kind;
}

/** Fisher-Yates with a seeded generator. Never mutates the input array. */
export function applyOrder<T>(items: readonly T[], order: OrderSpec): T[] {
  const out = [...items];
  if (order.kind === 'forward') return out;
  if (order.kind === 'reverse') return out.reverse();
  const next = rng(order.seed);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
