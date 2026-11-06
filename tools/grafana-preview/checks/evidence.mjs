// Exercise the visible-page/inspector evidence seam through real Grafana and Scenes.
// Faults are browser-local; fixture controls and upstream data are unchanged.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from './playwright.mjs';

const base = new URL(process.env.BASE ?? 'http://127.0.0.1:3431/a/homelab-observability-app/explore');
assert(['localhost', '127.0.0.1'].includes(base.hostname), 'Only use an isolated local Grafana');
const out = path.resolve(process.env.EVIDENCE ?? 'evidence/inspector');
fs.mkdirSync(out, { recursive: true });
const checks = [], errors = [];
const check = (name, ok, detail) => { checks.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}`); };
const browser = await chromium.launch(launchOptions);
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
page.on('pageerror', (error) => errors.push(error.message));
const drawer = page.locator('[data-testid="compare-inspector"]');
const csv = async () => {
  const downloaded = page.waitForEvent('download');
  await drawer.getByRole('button', { name: 'CSV', exact: true }).click();
  return fs.readFileSync(await (await downloaded).path(), 'utf8');
};
const refresh = () => page.locator('[data-testid="data-testid RefreshPicker run button"]').evaluate((button) => button.click());
const waiting = [];
const blocked = async (route) => {
  if (!route.request().postDataJSON()?.queries?.some((query) => query.datasource?.uid === 'comparison')) return route.continue();
  const fail = await new Promise((release) => waiting.push(release));
  return fail ? route.abort('failed') : route.continue();
};
const restore = async () => {
  await page.unroute('**/api/ds/query**', blocked);
  waiting.splice(0).forEach((release) => release(false));
};
const patch = () => page.route('**/api/ds/query**', blocked);
const navigate = (values) => page.evaluate(async (params) => {
  const { locationService } = await window.System.import('@grafana/runtime');
  locationService.partial(params);
}, values);

try {
  for (const scope of [
    { name: 'Home', params: { focus: 'Overview', inspect: 'briefing' }, surface: '[data-card="briefing"] [data-freshness]' },
    { name: 'Spark host', params: { focus: 'Sparks', view: 'host', entity: 'spark-1', inspect: 'spark:host:memory-history' }, surface: '.lab-domain' },
  ]) {
    base.search = new URLSearchParams({ source: 'replay', ...scope.params }).toString();
    await page.goto(base.href, { waitUntil: 'networkidle' });
    await drawer.getByRole('button', { name: 'CSV', exact: true }).waitFor();
    const before = await csv();
    assert(before.trim().split(/\r\n|\n|\r/).length >= 2, 'Healthy evidence must contain observations');
    await patch();
    await refresh();
    await page.waitForFunction((selector) => document.querySelector(selector)?.getAttribute('data-freshness') === 'loading', scope.surface);
    await drawer.getByRole('status').filter({ hasText: 'Updating this page' }).waitFor();
    check(`${scope.name}: pending inspector CSV matches the retained page evidence`, await csv() === before, await drawer.getByRole('status').innerText());
    waiting.splice(0).forEach((release) => release(true));
    await page.waitForFunction((selector) => document.querySelector(selector)?.getAttribute('data-freshness') === 'stale', scope.surface);
    await drawer.getByRole('status').filter({ hasText: 'Retained values were received' }).waitFor();
    check(`${scope.name}: failed inspector CSV preserves the same dated evidence`, await csv() === before, await drawer.getByRole('status').innerText());
    await page.screenshot({ path: path.join(out, `${scope.name === 'Home' ? 'home' : 'host'}-retained.png`), fullPage: true });
    await restore();
    await refresh();
    await drawer.getByRole('status').waitFor({ state: 'detached' });
    check(`${scope.name}: recovery restores current evidence`, await csv() === before);
    if (scope.name === 'Spark host') {
      await navigate({ entity: 'spark-2' });
      await page.waitForSelector('.lab-domain[data-entity="spark-2"]');
      await drawer.getByRole('button', { name: 'CSV', exact: true }).waitFor();
      await drawer.getByRole('combobox', { name: 'Frame' }).click();
      await page.getByRole('option').first().waitFor();
      const labels = await page.getByRole('option').allInnerTexts();
      check('Resource switch scopes inspector to the selected host', labels.length > 0 && labels.every((label) => /spark-2/.test(label) && !/spark-1/.test(label)), labels);
      await page.keyboard.press('Escape');
      await navigate({ from: '1000', to: '2000' });
      await page.waitForLoadState('networkidle');
      await page.waitForFunction(() => !document.querySelector('[data-testid="compare-inspector"] button')?.textContent?.includes('CSV') && !document.querySelector('.lab-domain')?.innerText.includes('Updating this page'));
      const text = await page.getByRole('dialog').innerText();
      check('Out-of-capture range cannot expose old host rows or CSV', !await drawer.getByRole('button', { name: 'CSV', exact: true }).count() && /no definition|No frames|No observations|no observations/.test(text), text.slice(0, 1200));
    }
  }
  check('No JavaScript exceptions during evidence transitions', errors.length === 0, errors);
} catch (error) {
  check('Evidence proof completes', false, { message: error.message, stack: error.stack, url: page.url() });
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  await restore().catch(() => {});
  await browser.close();
  fs.writeFileSync(path.join(out, 'evidence.json'), JSON.stringify({ checks, errors }, null, 2));
}
if (checks.some((item) => !item.ok)) process.exitCode = 1;
