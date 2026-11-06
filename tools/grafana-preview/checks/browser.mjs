import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from './playwright.mjs';

const base = process.env.BASE ?? 'http://127.0.0.1:3431/a/homelab-observability-app/explore';
assert(['localhost', '127.0.0.1'].includes(new URL(base).hostname), 'Acceptance only runs against a local preview');
const out = path.resolve(process.env.EVIDENCE ?? 'evidence/acceptance');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch(launchOptions);
const checks = [], errors = [], responses = [];
const check = (name, ok, detail) => { checks.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}`); };
const routes = {
  Overview: [''], Kubernetes: ['overview', 'workloads', 'storage', 'resource'], Proxmox: ['overview', 'guests', 'backups', 'guest', 'node'],
  Sparks: ['hardware', 'inference', 'host', 'deployment'], Athena: ['overview', 'storage', 'drives', 'incident', 'drive'],
  Network: ['paths', 'devices', 'path', 'device', 'port'], Power: ['overview', 'unit'], Devices: ['overview', 'host', 'time'],
};
const url = (focus, view, extras = {}) => {
  const target = new URL(base);
  for (const [key, value] of Object.entries({ focus, view, ...extras })) if (value !== '' && value != null) target.searchParams.set(key, value);
  return target.href;
};
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on('pageerror', (error) => errors.push({ url: page.url(), message: error.message }));
page.on('response', async (response) => {
  if (!response.url().includes('/api/ds/query')) return;
  let results;
  try { const body = await response.json(); results = Object.entries(body.results ?? {}).map(([ref, result]) => ({ ref, status: result.status, frames: result.frames?.length ?? 0, error: result.error })); } catch { /* status below still records transport failures */ }
  responses.push({ status: response.status(), results });
});
const settle = async (focus) => {
  await page.waitForSelector(`[data-host="scenes"][data-focus="${focus}"]`, { timeout: 45000 });
  if (focus !== 'Overview') {
    await page.waitForSelector('.lab-domain', { timeout: 30000 });
    await page.waitForFunction(() => document.querySelector('.lab-domain')?.getAttribute('data-freshness') !== 'loading', undefined, { timeout: 30000 });
  }
  await page.waitForLoadState('networkidle', { timeout: 30000 });
};

try {
  for (const [focus, pages] of Object.entries(routes)) {
    for (const view of pages) {
      await page.goto(url(focus, view), { waitUntil: 'domcontentloaded' });
      await settle(focus);
      const state = await page.evaluate(() => ({
        focus: document.querySelector('[data-host="scenes"]')?.getAttribute('data-focus'),
        page: document.querySelector('.lab-domain')?.getAttribute('data-page'),
        overflow: document.documentElement.scrollWidth > innerWidth,
        nav: document.querySelectorAll('.sys').length,
        selected: document.querySelectorAll('.sys[aria-current="page"]').length,
        charts: document.querySelectorAll('[data-chart]').length,
        painted: [...document.querySelectorAll('[data-chart] canvas')].filter((canvas) => {
          try { const data = canvas.getContext('2d')?.getImageData(0, 0, canvas.width, canvas.height).data; return data?.some((value, index) => index % 4 === 3 && value > 0); } catch { return false; }
        }).length,
      }));
      check(`${focus}/${view || 'home'} renders in the shared shell`, state.focus === focus && (focus === 'Overview' || state.page === view) && state.nav === 8 && state.selected === 1 && !state.overflow, state);
      if (focus === 'Sparks' && view === 'hardware') {
        check('captured Sparks histories paint native charts', state.charts === 7 && state.painted === 7, state);
        const palette = await page.locator('[data-chart] canvas').evaluateAll((canvases) => {
          const colors = [[77,176,255], [63,230,196], [198,133,255], [140,203,255], [132,238,217], [221,175,244]];
          const hits = colors.map(() => 0);
          for (const canvas of canvases) {
            const pixels = canvas.getContext('2d')?.getImageData(0, 0, canvas.width, canvas.height).data ?? [];
            for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 0) colors.forEach((rgb, at) => {
              if (rgb.every((channel, offset) => pixels[i + offset] === channel)) hits[at]++;
            });
          }
          return hits;
        });
        check('native lines use the preview palette without a theme adapter', palette.reduce((a, b) => a + b, 0) > 300, palette);
      }
      if (view === pages[0]) await page.screenshot({ path: path.join(out, `${focus.toLowerCase()}-desktop.png`), fullPage: true });
    }
    // Exercise a real, encoded resource identity from the rendered dashboard, rather than guessing label formats.
    if (focus !== 'Overview') {
      await page.goto(url(focus, pages[0]), { waitUntil: 'domcontentloaded' }); await settle(focus);
      const resource = await page.locator('.lab-domain a').evaluateAll((links) => links.map((link) => link.href).find((href) => { const parsed = new URL(href); return parsed.pathname.includes('/a/homelab-observability-app/') && parsed.searchParams.get('entity'); }));
      if (resource) {
        await page.goto(resource, { waitUntil: 'domcontentloaded' }); await settle(focus);
        const expected = new URL(resource).searchParams;
        check(`${focus} opens an observed resource identity`, await page.locator('.lab-domain').getAttribute('data-entity') === expected.get('entity') && await page.locator('.lab-domain').getAttribute('data-page') === expected.get('view'));
        if (focus === 'Sparks') check('selected Spark charts exclude the other host', !(await page.locator('[data-chart]').allInnerTexts()).join(' ').includes('spark-2'));
        await page.screenshot({ path: path.join(out, `${focus.toLowerCase()}-resource.png`), fullPage: true });
      } else check(`${focus} supplies an observed resource link`, false);
    }
  }

  await page.goto(url('Overview', ''), { waitUntil: 'domcontentloaded' }); await settle('Overview');
  const incident = page.locator('[data-item="probe/athena-dsm"]');
  if (await incident.count()) {
    const before = new URL(page.url()).searchParams;
    await incident.click(); await settle('Athena');
    const after = new URL(page.url()).searchParams;
    check('Home resolved incident opens Athena evidence with the same window', after.get('view') === 'incident' && after.get('entity') === 'athena-dsm' && before.get('from') === after.get('from') && before.get('to') === after.get('to'));
    await page.goBack(); await settle('Overview');
    check('Back returns from incident evidence to the overview', new URL(page.url()).searchParams.get('focus') === 'Overview');
    await page.goForward(); await settle('Athena');
    check('Forward restores the incident resource', new URL(page.url()).searchParams.get('entity') === 'athena-dsm');
  } else check('recorded incident is available in the review list', false);

  await page.goto(url('Sparks', 'inference', { from: '1793487600000', to: '1793491500000' }), { waitUntil: 'domcontentloaded' }); await settle('Sparks');
  const historicalText = await page.locator('.lab-domain').innerText();
  check('historical inference is a dated separate observation window', /Historical inference/.test(historicalText) && /GLM/.test(historicalText), historicalText.slice(0, 1800));
  await page.screenshot({ path: path.join(out, 'sparks-historical-inference.png'), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  for (const [focus, pages] of Object.entries(routes)) {
    await page.goto(url(focus, pages[0]), { waitUntil: 'domcontentloaded' }); await settle(focus);
    const layout = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth > innerWidth, nav: [...document.querySelectorAll('.sys')].map((el) => ({ text: el.innerText, left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right })) }));
    check(`${focus} phone layout keeps all navigation reachable`, !layout.overflow && layout.nav.length === 8 && layout.nav.every((item) => item.left >= 0 && item.right <= 391), layout);
    await page.screenshot({ path: path.join(out, `${focus.toLowerCase()}-phone.png`), fullPage: true });
  }
  check('no JavaScript exceptions across the investigation paths', errors.length === 0, errors);
  check('Grafana datasource requests have no transport failures', responses.length > 0 && responses.every((response) => response.status === 200), { requests: responses.length });
  check('replay queries have no evaluation errors', responses.flatMap((response) => response.results ?? []).every((result) => !result.error && (result.status == null || result.status === 200)));
} catch (error) {
  check('acceptance completed', false, { message: error.message, stack: error.stack, url: page.url() });
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }).catch(() => {});
} finally {
  fs.writeFileSync(path.join(out, 'acceptance.json'), JSON.stringify({ checks, errors, responses }, null, 2));
  await browser.close();
}
if (checks.some((item) => !item.ok)) process.exitCode = 1;
