// Minimal real-Grafana runtime proof. Only test-a/test-b fixture controls are changed; always reset in finally.
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from '../checks/playwright.mjs';
const lanes = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = path.resolve(process.argv[3]);
fs.mkdirSync(out, { recursive: true });
for (const lane of ['test-a', 'test-b']) {
  if (!lanes[lane]) throw new Error('Smoke needs both test-a and test-b; launch all lanes first.');
  if (!['127.0.0.1', 'localhost'].includes(new URL(lanes[lane].url).hostname)) throw new Error('Smoke only accepts isolated localhost Grafana.');
  const health = await (await fetch(lanes[lane].fixture + '/health')).json();
  if (health.lane !== lane) throw new Error(`Wrong fixture lane: ${JSON.stringify(health)}`);
}
const checks = [], errors = [], requests = [];
const startedAt = Date.now();
const check = (name, ok, detail) => { checks.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail == null ? '' : `: ${JSON.stringify(detail)}`}`); };
const control = async (lane, value) => {
  const response = await fetch(lanes[lane].fixture + '/control', { method: 'POST', body: JSON.stringify(value) });
  if (!response.ok) throw new Error(`Control failed: ${await response.text()}`);
};
const browser = await chromium.launch(launchOptions);
const contexts = [];
const open = async (lane) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } }); contexts.push(context);
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push({ lane, message: error.message }));
  page.on('request', (request) => { if (request.url().includes('/api/ds/query')) requests.push({ lane, queries: request.postDataJSON()?.queries?.map((q) => ({ refId: q.refId, datasource: q.datasource })) }); });
  return page;
};
const briefing = async (page) => {
  await page.waitForSelector('[data-host="scenes"]', { timeout: 45000 });
  await page.waitForFunction(() => {
    const el = document.querySelector('[data-card="briefing"] [data-state]');
    return el && !['Loading'].includes(el.getAttribute('data-state'));
  }, undefined, { timeout: 30000 });
  await page.waitForLoadState('networkidle');
  return page.locator('[data-card="briefing"]').evaluate((card) => ({ state: card.querySelector('[data-state]')?.getAttribute('data-state'), freshness: card.querySelector('[data-freshness]')?.getAttribute('data-freshness'), errored: card.querySelector('[data-errored]')?.getAttribute('data-errored'), text: card.innerText }));
};
const load = async (page, lane, suffix = '') => { await page.goto(lanes[lane].url + suffix, { waitUntil: 'domcontentloaded' }); return briefing(page); };
const shot = (page, name) => page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
try {
  await control('test-a', {}); await control('test-b', {});
  const a = await open('test-a'), b = await open('test-b');
  const healthyA = await load(a, 'test-a'), healthyB = await load(b, 'test-b');
  check('recorded Home renders a dated, fresh response', healthyA.freshness === 'fresh' && /at window end/.test(healthyA.state), healthyA);
  check('independent lane has the same baseline', healthyB.state === healthyA.state && healthyB.freshness === healthyA.freshness, healthyB);
  await shot(a, 'home-baseline');

  await control('test-a', { failRefs: '^(H_|M_|R_)' });
  const failed = await load(a, 'test-a');
  check('query failure cannot report no action needed', !/^No action needed/.test(failed.state) && failed.errored === 'true', failed);
  await shot(a, 'home-failure');
  const unchanged = await load(b, 'test-b');
  check('lane-a failure leaves lane-b state unchanged', unchanged.state === healthyB.state && unchanged.freshness === healthyB.freshness && !unchanged.errored, unchanged);
  const bErrors = await (await fetch(lanes['test-b'].fixture + '/logs?phase=error')).json();
  check('lane-b upstream logs contain no injected error', bErrors.length === 0);

  await control('test-a', { emptyRefs: '^(H_|M_|R_)' });
  const empty = await load(a, 'test-a');
  check('successful empty is unknown, never a healthy zero', !/^No action needed/.test(empty.state) && ['unavailable', 'empty'].includes(empty.freshness), empty);
  await shot(a, 'home-empty');

  await control('test-a', {});
  const recovered = await load(a, 'test-a');
  check('reset recovers through the real Grafana datasource', recovered.state === healthyA.state && recovered.freshness === healthyA.freshness && !recovered.errored, recovered);
  await load(a, 'test-a', '?focus=Sparks&view=host&entity=not-an-observed-host');
  await a.waitForSelector('[data-section="host-not-found"]', { timeout: 30000 });
  check('missing resource identity stays explicitly unavailable', /Host unavailable/.test(await a.locator('[data-section="host-not-found"]').innerText()));
  await shot(a, 'missing-entity');

  // A live navigation contract check intercepts requests locally; smoke never queries the production forward.
  await a.route('**/api/ds/query*', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ results: Object.fromEntries((route.request().postDataJSON()?.queries ?? []).map((q) => [q.refId, { status: 200, frames: [] }])) }) }));
  await load(a, 'test-a', '?source=live&focus=Sparks&view=hardware&entity=spark-1&from=2026-11-01T01%3A27%3A04.636Z&to=2026-11-02T01%3A27%3A04.636Z');
  const nativeLinks = await a.locator('.lab-domain a[href^="/d/"]').evaluateAll((links) => links.map((a) => a.getAttribute('href')));
  check('live native drill-downs explicitly select live and preserve range', nativeLinks.length > 0 && nativeLinks.every((href) => { const p = new URL(href, 'http://preview.invalid').searchParams; return p.get('var-datasource') === 'comparison-live' && p.has('from') && p.has('to'); }), nativeLinks);
  await a.route('**/api/datasources/proxy/uid/comparison-live/**', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: [] }) }));
  await a.route('**/api/datasources/uid/comparison-live/resources/**', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: [] }) }));
  const offset = requests.length;
  await a.goto(new URL(nativeLinks[0], lanes['test-a'].url).href, { waitUntil: 'networkidle' });
  const nativeQueries = requests.slice(offset).flatMap((r) => r.queries ?? []);
  check('native v1 copy actually queries the selected live datasource', nativeQueries.length > 0 && nativeQueries.every((q) => q.datasource?.uid === 'comparison-live'), { count: nativeQueries.length, datasources: [...new Set(nativeQueries.map((q) => q.datasource?.uid))] });
  await shot(a, 'native-live-intercepted');
  await a.goto(new URL('/d/home?var-datasource=comparison-live&from=2026-11-01T01%3A27%3A04.636Z&to=2026-11-02T01%3A27%3A04.636Z', lanes['test-a'].url).href, { waitUntil: 'networkidle' });
  const v2Offset = requests.length;
  // A native v2 reload exercises URL variable restoration as well as its Prometheus panel source.
  await a.reload({ waitUntil: 'networkidle' });
  const v2Queries = requests.slice(v2Offset).flatMap((r) => r.queries ?? []);
  check('native v2 copy actually queries the selected live datasource after reload', v2Queries.length > 0 && v2Queries.every((q) => q.datasource?.uid === 'comparison-live'), { count: v2Queries.length, datasources: [...new Set(v2Queries.map((q) => q.datasource?.uid))] });
  check('no JavaScript exceptions', errors.length === 0, errors);
} finally {
  await control('test-a', {}); await control('test-b', {});
  await browser.close();
  for (const lane of ['test-a', 'test-b']) fs.writeFileSync(path.join(out, `${lane}-requests.json`), JSON.stringify(await (await fetch(lanes[lane].fixture + `/logs?since=${startedAt}`)).json(), null, 2));
  fs.writeFileSync(path.join(out, 'smoke.json'), JSON.stringify({ checks, errors, requests }, null, 2));
}
if (checks.some((c) => !c.ok)) process.exitCode = 1;
