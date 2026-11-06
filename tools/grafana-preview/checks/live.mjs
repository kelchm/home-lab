// Opt-in read-only check. Requires a separately established VM forward; never substitutes replay.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from './playwright.mjs';

const base = new URL(process.env.BASE ?? 'http://127.0.0.1:3431/a/homelab-observability-app/explore');
assert(['localhost', '127.0.0.1'].includes(base.hostname), 'Only use the isolated local Grafana');
base.search = '?source=live&from=now-24h&to=now';
const out = path.resolve(process.env.EVIDENCE ?? 'evidence/live');
fs.mkdirSync(out, { recursive: true });
const started = Date.now(), responses = [], errors = [], views = [], requests = [];
const browser = await chromium.launch(launchOptions);
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on('pageerror', (error) => errors.push(error.message));
page.on('request', (request) => {
  if (request.url().includes('/api/ds/query')) requests.push(...(request.postDataJSON()?.queries ?? []).map(({ refId, datasource }) => ({ refId, datasource: datasource?.uid })));
});
page.on('response', async (response) => {
  if (!response.url().includes('/api/ds/query')) return;
  try {
    const body = await response.json();
    responses.push({ status: response.status(), results: Object.entries(body.results ?? {}).map(([ref, result]) => ({
      ref, status: result.status, error: result.error, frames: result.frames?.length ?? 0,
      rows: (result.frames ?? []).reduce((sum, frame) => sum + (frame.data?.values?.[0]?.length ?? 0), 0),
      latest: Math.max(0, ...(result.frames ?? []).flatMap((frame) => {
        const i = frame.schema?.fields?.findIndex((field) => field.type === 'time');
        return i >= 0 ? (frame.data?.values?.[i] ?? []) : [];
      })),
    })) });
  } catch (error) { errors.push(`Query response: ${error.message}`); }
});

try {
  await page.goto(base.href, { waitUntil: 'domcontentloaded' });
  for (const focus of ['Overview', 'Kubernetes', 'Proxmox', 'Sparks', 'Athena', 'Network', 'Power', 'Devices']) {
    if (focus !== 'Overview') await page.locator(`a[data-system="${focus}"]`).click();
    await page.waitForSelector(`[data-host="scenes"][data-focus="${focus}"]`);
    await page.waitForLoadState('networkidle');
    if (focus !== 'Overview') await page.waitForFunction(() => document.querySelector('.lab-domain')?.getAttribute('data-freshness') !== 'loading');
    const params = new URL(page.url()).searchParams;
    assert.equal(params.get('source'), 'live');
    assert.equal(params.get('from'), 'now-24h');
    views.push({ focus, freshness: await page.locator('.lab-domain').getAttribute('data-freshness').catch(() => null) });
    await page.screenshot({ path: path.join(out, `${focus.toLowerCase()}-live.png`), fullPage: true });
    console.log(`Visited ${focus} with live datasource`);
  }
} catch (error) { errors.push(error.message); }
finally { await browser.close(); }

const results = responses.flatMap((response) => response.results);
const checks = {
  allViews: views.length === 8,
  allQueriesLive: requests.length > 0 && requests.every((query) => query.datasource === 'comparison-live'),
  transport: responses.length > 0 && responses.every((response) => response.status === 200),
  evaluation: results.every((result) => !result.error && (result.status == null || result.status === 200)),
  currentEvidence: results.some((result) => result.latest >= started - 300000),
  noExceptions: errors.length === 0,
};
fs.writeFileSync(path.join(out, 'live.json'), JSON.stringify({ started, checks, views, requests, responses, errors }, null, 2));
console.log(JSON.stringify({ checks, requests: requests.length, populated: results.filter((result) => result.rows > 0).length, empty: results.filter((result) => result.rows === 0).length, errors }, null, 2));
if (Object.values(checks).some((ok) => !ok)) process.exitCode = 1;
