// Browser-local fault injection through the actual Scenes runner. No server or fixture state is changed.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from './playwright.mjs';

const base = new URL(process.env.BASE ?? 'http://127.0.0.1:3433/a/homelab-observability-app/explore');
assert(['localhost', '127.0.0.1'].includes(base.hostname), 'Only use an isolated local Grafana');
base.search = '?focus=Overview&source=replay';
const out = path.resolve(process.env.EVIDENCE ?? 'evidence/request-failure');
fs.mkdirSync(out, { recursive: true });
const checks = [];
const browser = await chromium.launch(launchOptions);
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const state = () => page.locator('[data-card="briefing"]').evaluate((card) => ({
  freshness: card.querySelector('[data-freshness]')?.getAttribute('data-freshness'),
  state: card.querySelector('[data-state]')?.getAttribute('data-state'), text: card.innerText,
}));
const refresh = () => page.locator('[data-testid="data-testid RefreshPicker run button"]').click();
try {
  await page.goto(base.href, { waitUntil: 'networkidle' });
  await page.waitForSelector('[data-card="briefing"] [data-freshness="fresh"]');
  const before = await state();
  await page.evaluate(async () => {
    const runtime = await window.System.import('@grafana/runtime');
    const service = runtime.getDataSourceSrv();
    const get = service.get;
    window.restorePreviewDatasource = () => { service.get = get; };
    service.get = (...args) => args[0]?.uid === 'comparison'
      ? Promise.reject(new Error('Injected datasource resolution failure'))
      : get.apply(service, args);
  });
  await refresh();
  await page.waitForSelector('[data-card="briefing"] [data-freshness="stale"]');
  const failed = await state();
  checks.push({ name: 'old rows from a real Scenes datasource-resolution failure remain stale', ok: failed.freshness === 'stale' && !/^No action needed/.test(failed.state ?? ''), before, failed });
  await page.screenshot({ path: path.join(out, 'retained-request-failure.png'), fullPage: true });
  await page.evaluate(() => window.restorePreviewDatasource());
  await refresh();
  await page.waitForSelector('[data-card="briefing"] [data-freshness="fresh"]');
  const recovered = await state();
  checks.push({ name: 'a successful refresh restores the original dated conclusion', ok: recovered.freshness === 'fresh' && recovered.state === before.state, recovered });
} catch (error) {
  checks.push({ name: 'request failure proof completes', ok: false, error: error.message });
} finally {
  await page.evaluate(() => window.restorePreviewDatasource?.()).catch(() => {});
  await browser.close();
  fs.writeFileSync(path.join(out, 'request-failure.json'), JSON.stringify({ checks }, null, 2));
}
console.log(JSON.stringify(checks, null, 2));
if (checks.some((check) => !check.ok)) process.exitCode = 1;
