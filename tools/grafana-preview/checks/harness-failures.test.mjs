import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { execFile } from 'node:child_process';

const root = path.resolve(import.meta.dirname, '..');
const run = (script, args, env) => new Promise((resolve) => execFile(process.execPath, [path.join(root, script), ...args], { env: { ...process.env, ...env }, timeout: 10000 }, (error, stdout, stderr) => resolve({ code: error?.code ?? 0, stdout, stderr })));
const fixture = (t) => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'harness failure space '));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  const module = path.join(temp, 'playwright.mjs'), closed = path.join(temp, 'closed');
  fs.writeFileSync(module, `import fs from 'node:fs';
    export const chromium = { launch: async () => ({
      newContext: async () => { throw new Error('original context failure'); },
      newPage: async () => ({ goto: async () => {}, waitForSelector: async () => { if (process.env.FAKE_MODE === 'missing') throw new Error('rendered selector absent'); }, evaluate: async () => ({ problems: [], fits: [] }) }),
      close: async () => { fs.writeFileSync(process.env.CLOSED, 'closed'); },
    }) };
  `);
  return { temp, closed, env: { PLAYWRIGHT_MODULE: module, CLOSED: closed, EVIDENCE: temp, BASE: 'http://127.0.0.1:1' } };
};

test('a harness exception replaces stale passing evidence and closes the browser', async (t) => {
  const { temp, closed, env } = fixture(t);
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({ results: [{ ok: true }] }));
  const result = await run('scenes/tests/browser/harness/check.mjs', [], env);
  assert.notEqual(result.code, 0);
  assert(fs.existsSync(closed));
  const evidence = JSON.parse(fs.readFileSync(path.join(temp, 'results.json')));
  assert(evidence.results.some((item) => !item.ok && item.detail.message === 'original context failure'));
});

test('unknown browser proof modes fail before replacing existing evidence', async (t) => {
  const { temp, closed, env } = fixture(t);
  const file = path.join(temp, 'browser-proof.json');
  fs.writeFileSync(file, 'old evidence');
  const result = await run('scenes/tests/browser/proof.mjs', ['typo'], env);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /Unknown proof mode/);
  assert.equal(fs.readFileSync(file, 'utf8'), 'old evidence');
  assert.equal(fs.existsSync(closed), false);
});

test('a failed shared capture preserves page errors and continues with the next spec', async (t) => {
  const { temp, closed, env } = fixture(t);
  const module = path.join(temp, 'capture-playwright.mjs');
  fs.writeFileSync(module, `import fs from 'node:fs';
    let index = 0;
    export const chromium = { launch: async () => ({
      newPage: async () => { const first = index++ === 0, handlers = {}; return {
        on: (name, fn) => { handlers[name] = fn; },
        goto: async () => { if (first) handlers.pageerror(new Error('original render failure')); },
        waitForSelector: async () => { if (first) throw new Error('rendered selector absent'); },
        screenshot: async ({path}) => { fs.writeFileSync(path, 'captured'); },
        evaluate: async () => 0,
        close: async () => fs.appendFileSync(process.env.CLOSED + '.pages', 'closed\\n'),
      }; },
      close: async () => { fs.writeFileSync(process.env.CLOSED, 'closed'); },
    }) };
  `);
  const result = await run('shared/harness/shoot.mjs', ['broken|?view=briefing', 'healthy|?view=machines'], { ...env, PLAYWRIGHT_MODULE: module, OUT: temp });
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /broken: original render failure/);
  assert.match(result.stderr, /broken: capture failed: rendered selector absent/);
  assert.equal(fs.readFileSync(path.join(temp, 'healthy.png'), 'utf8'), 'captured');
  assert.equal(fs.readFileSync(closed + '.pages', 'utf8').trim().split('\n').length, 2);
  assert(fs.existsSync(closed));
});

test('layout failure closes the browser and an empty measured layout fails', async (t) => {
  for (const mode of ['missing', 'empty']) {
    const { closed, env } = fixture(t);
    const result = await run('shared/harness/layout-check.mjs', [], { ...env, FAKE_MODE: mode });
    assert.notEqual(result.code, 0);
    assert.match(result.stderr, mode === 'missing' ? /rendered selector absent/ : /no layout surfaces measured/);
    assert(fs.existsSync(closed));
  }
});

test('failed smoke resets cannot mask the original failure or prevent browser cleanup and evidence', async (t) => {
  const { temp, closed, env } = fixture(t);
  const servers = [], lanes = {}, seenLogs = [];
  for (const lane of ['test-a', 'test-b']) {
    let controls = 0;
    const server = http.createServer((req, res) => {
      res.setHeader('content-type', 'application/json');
      if (req.url === '/health') return res.end(JSON.stringify({ lane }));
      if (req.url === '/control') { res.statusCode = ++controls > 1 ? 503 : 200; return res.end('{}'); }
      seenLogs.push(req.url);
      res.end('[]');
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    servers.push(server);
    const url = `http://127.0.0.1:${server.address().port}`;
    lanes[lane] = { url, fixture: url };
  }
  t.after(async () => Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve)))));
  const registry = path.join(temp, 'lanes.json');
  fs.writeFileSync(registry, JSON.stringify(lanes));
  const result = await run('runtime/smoke.mjs', [registry, temp], env);
  assert.notEqual(result.code, 0);
  assert(fs.existsSync(closed));
  const evidence = JSON.parse(fs.readFileSync(path.join(temp, 'smoke.json')));
  assert(evidence.checks.some((item) => item.name === 'smoke completed' && item.detail.message === 'original context failure'));
  assert.equal(evidence.checks.filter((item) => item.name.endsWith('controls reset') && !item.ok).length, 2);
  assert.equal(seenLogs.length, 2);
  assert(seenLogs.every((url) => /^\/logs\?since=\d+$/.test(url)));
  assert(fs.existsSync(path.join(temp, 'test-a-requests.json')));
  assert(fs.existsSync(path.join(temp, 'test-b-requests.json')));
});
