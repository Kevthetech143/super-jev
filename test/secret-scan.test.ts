import test from 'node:test';
import assert from 'node:assert/strict';
import { Jev } from '../src/jev.ts';
import { hasSecret, payloadHasSecret } from '../src/secret-scan.ts';

const request = (text: string) => ({
  state: { question: text },
  questions: { q: { type: 'choice', instructions: 'pick', options: ['a', 'b'] } }
}) as any;

test('Jev refuses to fetch a body with a secret anywhere in it (0 sends)', async () => {
  let sends = 0;
  const jev = new Jev({ apiKey: 'fake', fetch: (async () => { sends++; return new Response('{}', { status: 500 }); }) as any });
  for (const text of ['api_key = sk-live-9fQ2xZ7pL0aBcD3eF4', 'card 4000 0566 5566 5556', 'ghp_' + 'a1'.repeat(18)]) {
    await assert.rejects(jev.evaluate(request(text), new AbortController().signal), /contains a secret; not sent/);
  }
  assert.equal(sends, 0);
  await assert.rejects(jev.evaluate(request('what colour is the box?'), new AbortController().signal), /Jev HTTP 500/);
  assert.ok(sends > 0);
});

test('Node scan matches the Python has_secret cases from the shared pattern file', () => {
  assert.equal(hasSecret('1Password vault'), false);
  assert.equal(hasSecret('dated 2024-01-02 2024-01-03 2024-01-04'), false);
  assert.equal(hasSecret('see https://x.test/?id=1234123412341234'), false);
  assert.equal(hasSecret('token: Zx9Qp2Lm8Rt4Vb6Nc1Hs3Kd7'), true);
  assert.equal(payloadHasSecret({ a: [{ b: 'passwd: hunter2' }] }), true);
  assert.equal(payloadHasSecret({ a: [{ b: 'reset the password in settings' }] }), false);
  assert.equal(payloadHasSecret({ a: ['fine'] }), false);
});

test('a connection string password and a Slack webhook are secrets', () => {
  assert.equal(hasSecret("DB = 'postgres://appuser:hunter22x@db.example.test/main'"), true);
  assert.equal(hasSecret('redis://:s3cretpw9@cache.example.test:6379'), true);
  assert.equal(hasSecret('https://hooks.slack.com/services/T0AAAAAAA/B0BBBBBBB/abcDEF123456'), true);
  assert.equal(hasSecret('postgres://appuser@db.example.test/main and http://localhost:3000/a'), false);
  assert.equal(hasSecret('postgres://user:password@localhost:5432/app'), false);
  assert.equal(hasSecret('postgresql://USER:PASS@HOST:5432/DB'), false);
  assert.equal(hasSecret('amqp://guest:guest@localhost:5672'), false);
  assert.equal(hasSecret('http://localhost:3000?email=foo@bar.com'), false);
  assert.equal(hasSecret('https://example.com:8443?to=a@b.com'), false);
});

test('2000 random sha256 digests never trip the scan; real cards and keys still held', async () => {
  const { createHash, randomBytes } = await import('node:crypto');
  const digests = Array.from({ length: 2000 }, () => createHash('sha256').update(randomBytes(32)).digest('hex'));
  assert.deepEqual(digests.filter(hasSecret), []);
  assert.equal(payloadHasSecret({ sources: digests.map((sha256) => ({ path: '/x/a.md', description: 'notes', sha256 })) }), false);
  for (const t of ['card 4000 0566 5566 5556', '4000056655665556', '5500-0000-0000-0004', 'api_key = x9', 'ghp_' + 'a1'.repeat(18)]) assert.equal(hasSecret(t), true, t);
  assert.equal(payloadHasSecret({ sources: [{ description: 'card 4000056655665556', sha256: 'ab' }] }), true);
});

test('an escaped quote before a placeholder is not a secret; a real value after one still is', () => {
  const q = '\\"';
  for (const t of [`export TYPESAFE_API_KEY=${q}$(cat /path/to/key-file)${q}`, `API_KEY=${q}\${VAR}${q}`,
    "api_key=\\'<your key>\\'", `password=${q}$(pass show mail)${q}`]) assert.equal(hasSecret(t), false, t);
  for (const t of [`API_KEY=${q}sk-live-9fQ2xZ7pL0aBcD3eF4${q}`, `password=${q}hunter2xyz9${q}`,
    `password=${q}$3cret!Pass9${q}`, `password=${q}$uperSecret9${q}`, `API_KEY=${q}$9qP7vK2mR8wL6z${q}`,
    `password=${q}$(LiteralSecret9${q}`, `password=${q}\${LiteralSecret9${q}`, `password=${q}<LiteralSecret9${q}`,
    `API_KEY=${q}$(LiteralSecret9${q}`, `API_KEY=${q}\${LiteralSecret9${q}`, `API_KEY=${q}<LiteralSecret9${q}`,
    `password=${q}$(LiteralSecret9}${q}`, `password=${q}\${VAR}hunter2xyz9${q}`, `password=${q}$(cat f)hunter2xyz9${q}`,
    `password=${q}<x>hunter2xyz9${q}`, 'password="${VAR}hunter2xyz9"', 'password="$(cat f)hunter2xyz9"',
    'password="<x>hunter2xyz9"', 'password=${VAR}hunter2xyz9', 'api_key="${VAR}Zx9Qp2Lm8Rt4"']) assert.equal(hasSecret(t), true, t);
  for (const t of ['password="${VAR}"', 'password=$VAR', 'password=$VAR;', 'api_key=${VAR}', 'password: <your password>',
    `api_key=${q}\${VAR}${q}, x`]) assert.equal(hasSecret(t), false, t);
});

test('a card after another digit group and a 15-digit Amex are held; ordinary numbers are not', () => {
  for (const t of ['order 1234 4000 0566 5566 5556', 'order 1234-5678-4000-0566-5566-5556', '1234 4000056655665556',
    '3400 000000 00009', 'amex 3400-000000-00009', '340000000000009', 'card 3400 000000 00009 exp 09/28',
    '3400 000000 00009 ١']) assert.equal(hasSecret(t), true, t);
  for (const t of ['order 1234 5678 9012 3456 7890', '3782 822463 10006', '3882 822463 10005', 'x3400 000000 00009',
    '3782 822463-10005', 'call +1 (555) 123-4567', 'imei 356938035643809', 'fedex 123456789012']) assert.equal(hasSecret(t), false, t);
  // A hit inside the overlapping scan must not leave lastIndex on a shared regex for the next call.
  assert.equal(hasSecret('order 1234 4000 0566 5566 5556'), true);
  assert.equal(hasSecret('card 4000 0566 5566 5556'), true);
});

test('a spaced USPS tracking number is not a card; a card next to digits still is', () => {
  const trk = '9302 2110 4790 0005 3721 11'; // made-up, valid check digit; its first 16 digits pass Luhn
  for (const t of [trk, trk.replaceAll(' ', '-'), `tracking ${trk} delivered`]) assert.equal(hasSecret(t), false, t);
  for (const t of ['9302 2110 4790 0005 3721 12', `${trk} 5`, `${trk} ١`, 'card 4000 0566 5566 5556 123',
    `4000 0566 5566 5556 ${trk}`, `tracking ${trk}, card 4000 0566 5566 5556`,
    'call 9100000001 4000 0566 5566 5556 thanks', 'ref 920000 4000 0566 5566 5556']) assert.equal(hasSecret(t), true, t);
});

test('engine uuids under ticket and attemptId are not scanned: a uuid4 ending in 12 digits reads as a card (R6 flake)', () => {
  const u = 'd4b9227c-4460-444a-9183-059972976233';
  assert.equal(hasSecret(u), true);
  assert.equal(payloadHasSecret({ action: 'assist', attemptId: u, principal: 'me' }), false);
  assert.equal(payloadHasSecret({ action: 'approve', ticket: u, principal: 'me' }), false);
  assert.equal(payloadHasSecret({ action: 'approve', ticket: u, answer: 'card 4000056655665556' }), true);
});
