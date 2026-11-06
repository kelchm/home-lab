import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assembleCatalog, canonical, compileCatalog, expressionHash, findEntry, inferenceCaptures, matchesRef, replay } from './replay.mjs';
import { createReplayServer } from './server.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => JSON.parse(fs.readFileSync(path.join(root, p), 'utf8'));
const window = { from: 1000, to: 5000 };
const query = (refId, extra = {}) => ({ refId, key: refId, source: 'test', expr: 'sum(up)', range: false, instant: true, scope: 'test', ...extra });
const base = [{ ...query('H_up'), series: [{ labels: { instance: 'node-a' }, t: [500, 1000, 3000, 5000, 7000], v: [1, 1, 0, 1, 1] }] }];
const assemble = (domains = [], extra = {}) => assembleCatalog({ base, commonWindow: window, bounded: { ...window, instant: {}, range: {} }, inference: [], domains, ...extra });

test('canonicalization normalizes syntax whitespace without rewriting label values or tokens', () => {
  assert.equal(canonical('sum ( up )'), canonical('sum(up)'));
  assert.notEqual(canonical('up{instance="a b"}'), canonical('up{instance="ab"}'));
  assert.notEqual(canonical('foo or bar'), canonical('fooorbar'));
});

test('equal expressions retain aliases for failure control and clip out-of-window capture points', () => {
  const entries = assemble([query('D_test_up', { expr: 'sum (up)', capture: { kind: 'catalog', key: 'H_up' } })]);
  assert.equal(entries.length, 1);
  assert.deepEqual(entries[0].aliases, ['H_up', 'D_test_up']);
  assert.deepEqual(entries[0].series[0].t, [1000, 3000, 5000]);
  assert(matchesRef(entries[0], '^D_test_up$'));
  assert.deepEqual(replay(entries[0], { time: 5000, range: false, state: { emptyRefs: '^D_test_up$' } }), []);
  assert.deepEqual(replay(entries[0], { time: Date.now(), range: false }), []);
});

test('uncaptured expressions, ambiguous query modes, and wrong-mode capture locators stay explicit', () => {
  const entries = assemble([query('D_test_missing', { expr: 'missing_metric', capture: { kind: 'bounded', key: 'absent' } })]);
  assert.equal(entries[1].captureStatus, 'missing');
  assert.deepEqual(entries[1].series, []);
  assert.throws(() => assemble([query('D_test_bad', { range: true })]), /Invalid query/);
  assert.throws(() => assemble([query('H_up')]), /Duplicate query/);
  assert.equal(assemble([query('D_test_wrong', { expr: 'wrong', capture: { kind: 'bounded', key: 'wrong', range: true } })], { bounded: { ...window, range: { wrong: { expr: 'wrong', series: base[0].series } } } })[1].captureStatus, 'missing');
});

test('macro matching accepts Grafana duration expansion while exact definitions beat historical aliases', () => {
  const entries = compileCatalog([{ ...base[0], expressions: ['rate(up[$__rate_interval])'] }, { ...base[0], refId: 'actual', expr: 'other', expressions: ['other', 'sum(up)'] }]);
  assert.equal(findEntry(entries, 'rate(up[2m0s])', false).refId, 'H_up');
  assert.equal(findEntry(entries, 'sum(up)', false).refId, 'H_up');
  assert.equal(findEntry(entries, 'rate(up[2m0s])', true), undefined);
});

test('missing entity removes only matching labeled series and gaps preserve timestamps', () => {
  const e = assemble()[0];
  e.aliases = ['H_up'];
  assert.deepEqual(replay(e, { range: true, start: 1000, end: 5000, state: { missingEntity: 'node-a' } }), []);
  const points = replay(e, { range: true, start: 1000, end: 5000, state: { gapRefs: 'H_up', gapWindow: { from: 2500, to: 3500 } } })[0].values;
  assert.deepEqual(points, [[1, '1'], [5, '1']]);
});

test('instant aliases cannot borrow labels or points across disjoint capture windows', () => {
  const e = { ...assemble()[0], window: { from: 1000, to: 9000 }, segments: [
    { window: { from: 1000, to: 2000 }, series: [{ labels: { instance: 'historical' }, t: [2000], v: [1] }] },
    { window: { from: 8000, to: 9000 }, series: [{ labels: { instance: 'daily' }, t: [9000], v: [0] }] },
  ] };
  assert.deepEqual(replay(e, { time: 5000, range: false }), []);
  assert.deepEqual(replay(e, { time: 8500, range: false }), []);
  assert.deepEqual(replay(e, { time: 9000, range: false }).map((s) => s.metric.instance), ['daily']);
});

test('real inference metadata applies to all four anonymous context frames and stays in its dated window', () => {
  const responses = read('fixtures/inference.json');
  const result = responses[7].results.A;
  assert.equal(result.frames.length, 4);
  const expr = result.frames[0].schema.meta.executedQueryString.match(/^Expr: ([\s\S]*?)\nStep:/)[1];
  const captures = inferenceCaptures(responses);
  const capture = captures.get(`expr:${expressionHash(expr)}:false`);
  assert.equal(capture.series.length, 4);
  assert.deepEqual(capture.series.map((s) => s.labels.pool).sort(), ['0', '1', '2', '3']);
  assert.deepEqual(capture.window, { from: Date.parse('2026-10-31T23:00Z'), to: Date.parse('2026-11-01T00:05Z') });
  for (const s of capture.series) assert(s.t.every((t) => t >= capture.window.from && t <= capture.window.to));
  const catalog = assemble([query('D_test_context', { expr: 'live_context', capture: { kind: 'inference', key: `expr:${expressionHash(expr)}` } })], { inference: responses });
  assert.equal(catalog[1].series.length, 4);
  assert(catalog[1].provenance.every((p) => p.aliasDiffers && p.executedExprHash === expressionHash(p.executedExpr)));
});

test('two HTTP lanes isolate failure/empty/delay/cancel controls and scope request logs by aliases', async (t) => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'grafana-preview-test-'));
  const servers = [], urls = [];
  const catalog = assemble([query('D_test_up', { capture: { kind: 'catalog', key: 'H_up' } })]);
  for (const lane of ['test-a', 'test-b']) {
    const server = createReplayServer({ catalog, stateDir: path.join(temp, lane), lane });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    servers.push(server); urls.push(`http://127.0.0.1:${server.address().port}`);
  }
  t.after(async () => { await Promise.all(servers.map((s) => new Promise((r) => s.close(r)))); fs.rmSync(temp, { recursive: true }); });
  const control = (lane, state) => fetch(urls[lane] + '/control', { method: 'POST', body: JSON.stringify(state) });
  const q = (lane, expr = 'sum(up)', signal) => fetch(urls[lane] + '/api/v1/query?' + new URLSearchParams({ query: expr, time: '5' }), { signal });
  await control(0, { failRefs: '^D_test_up$' });
  assert.equal((await q(0)).status, 503);
  assert.equal((await q(1)).status, 200);
  assert.equal((await (await q(1)).json()).data.result.length, 1);
  await control(0, { emptyRefs: 'D_test_up' });
  assert.deepEqual((await (await q(0)).json()).data.result, []);
  assert.deepEqual((await (await q(0, 'uncaptured')).json()).data.result, []);
  await control(0, { delayMs: 5000, delayRefs: 'D_test_up' });
  const abort = new AbortController();
  const pending = q(0, 'sum(up)', abort.signal);
  await new Promise((r) => setTimeout(r, 50)); abort.abort();
  await assert.rejects(pending, /abort/i);
  await new Promise((r) => setTimeout(r, 30));
  const events = await (await fetch(urls[0] + '/logs?ref=D_test_up')).json();
  assert(events.every((e) => e.lane === 'test-a' && e.aliases.includes('D_test_up')));
  assert(events.some((e) => e.phase === 'cancelled'));
  assert.equal((await (await fetch(urls[1] + '/logs?phase=error')).json()).length, 0);
  await control(0, {});
  assert.equal((await (await q(0)).json()).data.result.length, 1);
  assert.equal((await control(0, { failRefs: '[' })).status, 400);
});
