import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { observeDatasource } from './datasource.mjs';
import { hasDashboardVariable, isFixturePath, isAnonymousStarsError } from './guards.mjs';

test('anonymous stars exception cannot hide other HTTP or console failures', () => {
  const origin = 'http://127.0.0.1:3435';
  const endpoint = '/apis/collections.grafana.app/v1alpha1/namespaces/default/stars?fieldSelector=metadata.name%3Duser-';
  const text = 'Failed to load resource: the server responded with a status of 403 (Forbidden)';
  const message = (url = origin + endpoint, value = text) => ({ text: () => value, location: () => ({ url }) });
  assert(isAnonymousStarsError(message(), origin));
  for (const url of [origin + '/api/ds/query', origin + endpoint.replace('user-', 'user-123'), 'https://example.com' + endpoint, origin + endpoint + '&other=1', '']) assert(!isAnonymousStarsError(message(url), origin));
  assert(!isAnonymousStarsError(message(undefined, text.replace('403', '500')), origin));
});

const request = (errorText = 'net::ERR_CONNECTION_RESET') => ({ url: () => 'http://127.0.0.1/api/ds/query?ds_type=prometheus', failure: () => ({ errorText }), postDataJSON: () => ({ queries: [{ refId: 'A' }] }) });
const response = (body, status = 200, req = request()) => ({ url: req.url, status: () => status, json: typeof body === 'function' ? body : async () => body, request: () => req });
const good = { results: { A: { status: 200, frames: [] } } };

test('large valid time vectors do not overflow the JavaScript argument limit', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  const values = Array.from({ length: 200_001 }, (_, i) => i);
  page.emit('response', response({ results: { A: { frames: [{ schema: { fields: [{ type: 'time' }] }, data: { values: [values] } }] } } }));
  assert.equal(await monitor.drain(), true);
  assert.equal(monitor.evaluationOk, true);
  assert.equal(monitor.responses[0].results[0].latest, 200_000);
  assert.equal(monitor.responses[0].results[0].rows, 200_001);
});

test('a stalled response body produces failed evidence without blocking cleanup or later recovering as success', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  let finish;
  page.emit('response', response(() => new Promise((resolve) => { finish = resolve; })));
  assert.equal(await monitor.drain(10), false);
  assert.equal(monitor.responses[0].incompleteBody, true);
  assert.match(monitor.responses[0].error, /did not finish within 10 ms/);
  assert.equal(monitor.evaluationOk, false);
  assert.equal(await monitor.drain(10), true);
  finish(good);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(monitor.evaluationOk, false);
});

test('datasource acceptance waits for body parsing and accepts an explicit successful empty query', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  let resolve;
  page.emit('response', response(() => new Promise((r) => { resolve = r; })));
  assert.equal(monitor.evaluationOk, false);
  let drained = false;
  const done = monitor.drain().then(() => { drained = true; });
  await Promise.resolve();
  assert.equal(drained, false);
  resolve(good);
  await done;
  assert.equal(monitor.evaluationOk, true);
  assert.equal(monitor.transportOk, true);
});

test('HTTP 200 cannot hide malformed JSON, absent results, missing refs or malformed per-query results', async () => {
  for (const body of [async () => { throw new SyntaxError('not JSON'); }, {}, { results: {} }, { results: [] }, { results: { B: { frames: [] } } }, { results: { A: null } }, { results: { A: {} } }]) {
    const page = new EventEmitter(), monitor = observeDatasource(page);
    page.emit('response', response(body));
    await monitor.drain();
    assert.equal(monitor.evaluationOk, false);
    assert(monitor.responses[0].error);
  }
});

test('HTTP and per-query failures remain failures alongside otherwise valid responses', async () => {
  for (const [body, status] of [[good, 503], [{ results: { A: { status: 500, error: 'query failed' } } }, 200]]) {
    const page = new EventEmitter(), monitor = observeDatasource(page);
    page.emit('response', response(body, status));
    await monitor.drain();
    assert.equal(monitor.transportOk && monitor.evaluationOk, false);
  }
});

test('transport failures count even without responses, while explicit navigation aborts are separate', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  const aborted = request('net::ERR_ABORTED');
  page.emit('request', aborted);
  await monitor.navigate(async () => {});
  page.emit('requestfailed', aborted);
  assert.equal(monitor.abortedNavigations.length, 1);
  assert.equal(monitor.failures.length, 0);
  const unexpectedAbort = request('net::ERR_ABORTED');
  page.emit('request', unexpectedAbort); page.emit('requestfailed', unexpectedAbort);
  const failed = request();
  page.emit('request', failed); page.emit('requestfailed', failed);
  assert.equal(monitor.failures.length, 2);
  assert.equal(monitor.transportOk, false);
});

test('bundle guards recognize root and nested fixture segments and both dashboard variable forms', () => {
  for (const input of ['fixtures/bounded.json', '../fixtures/a.json', 'C:\\repo\\fixtures\\bounded.json', '/a/fixtures/bounded.json']) assert(isFixturePath(input), input);
  for (const input of ['fixtures-not/a.json', 'src/notfixtures/a.json', 'fixtures.ts']) assert(!isFixturePath(input), input);
  for (const expr of ['up{job="$job"}', 'up{job="${job}"}', 'up{job="${job:regex}"}', 'up{job="$_private"}']) assert(hasDashboardVariable(expr), expr);
  for (const expr of ['rate(up[$__rate_interval])', 'rate(up[${__interval}])', 'label_replace(up,"a","$1","b","(.*)")']) assert(!hasDashboardVariable(expr), expr);
});


test('an intentionally interrupted response body is marked as navigation cancellation, not malformed JSON', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  page.emit('response', response(good));
  const req = request('net::ERR_ABORTED');
  let reject;
  page.emit('request', req);
  page.emit('response', response(() => new Promise((_resolve, fail) => { reject = fail; }), 200, req));
  await monitor.navigate(async () => {});
  page.emit('requestfailed', req);
  reject(new Error('Response body aborted'));
  await monitor.drain();
  assert.equal(monitor.responses[1].abortedNavigation, true);
  assert.equal(monitor.evaluationOk, true);
  assert.equal(monitor.transportOk, true);
});


test('a still-unanswered request cannot pass behind an earlier successful response', async () => {
  const page = new EventEmitter(), monitor = observeDatasource(page);
  page.emit('response', response(good));
  await monitor.drain();
  const pending = request();
  page.emit('request', pending);
  assert.equal(monitor.transportOk, false);
  assert.equal(monitor.inFlight.length, 1);
  page.emit('requestfinished', pending);
  assert.equal(monitor.transportOk, true);
});
