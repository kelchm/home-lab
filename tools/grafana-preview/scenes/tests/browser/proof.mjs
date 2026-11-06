// Real Grafana acceptance guard. BASE must name the isolated preview; it writes only scenes/evidence. No live queries unless LIVE_SMOKE=1.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { observeDatasource } from '../../../checks/datasource.mjs';
import { chromium, launchOptions } from '../../../checks/playwright.mjs';

const LANE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = path.resolve(process.env.EVIDENCE ?? path.join(LANE, 'evidence'));
const BASE = process.env.BASE;
if (!BASE || !['127.0.0.1', 'localhost'].includes(new URL(BASE).hostname)) throw new Error('Set BASE to the isolated localhost preview URL.');
const commonQueries = JSON.parse(fs.readFileSync(path.join(LANE, '../runtime/queries.json'), 'utf8')).common;
const networkQueries = JSON.parse(fs.readFileSync(path.join(LANE, '../shared/src/domains/network/queries.json'), 'utf8'));
const FOCUSES = ['Overview', 'Kubernetes', 'Proxmox', 'Sparks', 'Athena', 'Network', 'Power', 'Devices'];
const requestedRefs = [];
const HOME = '/a/homelab-observability-app/explore';

const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail === undefined ? '' : ` :: ${JSON.stringify(detail)}`}`);
};

const monitors = new Map();
const browsers = new Set();
const offset = () => requestedRefs.length;
const upstream = (from) => requestedRefs.slice(from);

async function open(width, height) {
  const browser = await chromium.launch(launchOptions);
  browsers.add(browser);
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const page = await context.newPage();
  const log = { errors: [], queries: 0, datasource: observeDatasource(page) };
  monitors.set(page, log.datasource);
  page.on('pageerror', (error) => log.errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      log.errors.push(`console: ${message.text().slice(0, 240)}`);
    }
  });
  page.on('request', (request) => {
    if (request.url().includes('/api/ds/query')) {
      log.queries += 1;
      const body = request.postDataJSON();
      requestedRefs.push(...(body?.queries ?? []).map((query) => query.refId));
    }
  });
  return { browser, page, log };
}

const settle = async (page) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(500);
  await monitors.get(page)?.drain();
};

const goto = async (page, query = '') => {
  await monitors.get(page).navigate(() => page.goto(`${BASE}${HOME}${query}`, { waitUntil: 'domcontentloaded' }));
  await page.waitForSelector('[data-host="scenes"]', { timeout: 30000 });
  await settle(page);
};

const shot = (page, name, fullPage = false) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
const query = (page) => new URL(page.url()).searchParams;
const clickSystem = (page, name) => page.locator(`a[data-system="${name}"]`).click();

async function desktop() {
  const { browser, page, log } = await open(1440, 1000);

  let from = offset();
  await goto(page);
  await shot(page, 'overview-1440');
  await shot(page, 'overview-1440-full', true);
  const first = upstream(from);
  check('first load runs the common queries once, in one browser request', first.length === commonQueries.length && new Set(first).size === commonQueries.length && log.queries === 1, { upstream: first.length, browser: log.queries });
  check('first load runs no Network-only query', !first.some((ref) => ref?.startsWith('D_')), undefined);

  const params = query(page);
  check('URL carries focus and the recorded window', params.get('focus') === 'Overview' && params.get('from') === '2026-11-01T01:27:04.636Z', Object.fromEntries(params));

  const pinned = await page.evaluate(async () => {
    const band = document.querySelector('[data-testid="pinned-band"]');
    const before = band.getBoundingClientRect().top;
    window.scrollTo(0, 900);
    await new Promise((resolve) => setTimeout(resolve, 200));
    const rect = band.getBoundingClientRect();
    return { before, top: rect.top, height: rect.height, scrollY: window.scrollY, picker: band.querySelector('[data-testid*="TimePicker"], button[aria-label*="Time range"]') !== null };
  });
  await shot(page, 'overview-1440-scrolled');
  check('navigation and Grafana controls stay pinned while scrolling', pinned.scrollY > 0 && pinned.top < pinned.before + 1 && pinned.top <= 100 && pinned.picker, pinned);
  await page.evaluate(() => window.scrollTo(0, 0));

  const natural = await page.evaluate(() =>
    ['activity', 'review', 'machines'].map((card) => {
      const element = document.querySelector(`[data-card="${card}"]`);
      const clipped = [...element.querySelectorAll('*')].filter((node) => /auto|scroll|hidden/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight + 2 && node.clientHeight > 40);
      return { card, height: Math.round(element.getBoundingClientRect().height), clipped: clipped.length };
    })
  );
  check('activity, review and Machines render at natural height with nothing clipped', natural.every((item) => item.height > 80 && item.clipped === 0), natural);

  await page.evaluate(() => (window.__sameDocument = true));
  from = offset();
  await clickSystem(page, 'Network');
  await page.waitForSelector('[data-host="scenes"][data-focus="Network"]');
  await settle(page);
  await shot(page, 'network-1440');
  await shot(page, 'network-1440-full', true);
  const network = upstream(from);
  check('Network link navigates in place, keeps the range and writes focus', (await page.evaluate(() => window.__sameDocument === true)) && query(page).get('focus') === 'Network' && query(page).get('from') === '2026-11-01T01:27:04.636Z', Object.fromEntries(query(page)));
  check('first Network visit runs only its declared queries, with no common re-run', network.length === networkQueries.length && network.every((ref) => networkQueries.some((query) => query.refId === ref)), { upstream: network.length });

  from = offset();
  await monitors.get(page).navigate(() => page.goBack());
  await page.waitForSelector('[data-host="scenes"][data-focus="Overview"]');
  await monitors.get(page).navigate(() => page.goForward());
  await page.waitForSelector('[data-host="scenes"][data-focus="Network"]');
  await settle(page);
  check('Back and Forward restore focus without running a query', upstream(from).length === 0, { upstream: upstream(from).length });

  from = offset();
  await monitors.get(page).navigate(() => page.reload({ waitUntil: 'domcontentloaded' }));
  await page.waitForSelector('[data-host="scenes"][data-focus="Network"]');
  await settle(page);
  check('reload restores Network and runs both sources once', upstream(from).length === commonQueries.length + networkQueries.length, { upstream: upstream(from).length });

  await goto(page, '?focus=Nope');
  check('an unknown focus falls back to Overview and corrects the URL', query(page).get('focus') === 'Overview' && (await page.locator('[data-host="scenes"][data-focus="Overview"]').count()) === 1, Object.fromEntries(query(page)));

  // Tools, as an anonymous Viewer.
  await goto(page, '?focus=Network');
  const chart = page.locator('[data-chart]').first();
  await chart.hover();
  await chart.locator('button[aria-label^="Menu for panel"], button[data-testid*="panel menu"]').first().click();
  await page.waitForSelector('[role="menu"]');
  const chartMenu = await page.locator('[role="menu"] [role="menuitem"]').allInnerTexts();
  await shot(page, 'tools-chart-menu');
  check('a VizPanel shows the configured public panel menu to a Viewer', chartMenu.some((item) => item.includes('Inspect data')) && chartMenu.some((item) => item.includes('Share')), chartMenu);
  check('a Viewer is not offered Explore', !chartMenu.some((item) => item.includes('Explore')) && (await page.locator('[data-chart] a', { hasText: 'Explore' }).count()) === 0, undefined);
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Tools for Daily briefing' }).focus();
  await page.keyboard.press('Enter');
  await page.waitForSelector('[role="menu"]');
  const cardMenu = await page.locator('[role="menu"] [role="menuitem"]').allInnerTexts();
  await shot(page, 'tools-card-menu');
  check('a natural-height card shows the same menu', cardMenu.join() === chartMenu.join(), cardMenu);
  await page.locator('[role="menu"] [role="menuitem"]', { hasText: 'Inspect data' }).click();
  await page.waitForSelector('[data-testid="compare-inspector"]');
  await settle(page);
  await shot(page, 'tools-inspect-data');
  check('Inspect data opens from a link, is labelled custom and shows a table', query(page).get('inspect') === 'briefing' && (await page.getByText("Custom inspector, not Grafana's").count()) === 1 && (await page.locator('[data-testid="compare-inspector"] [role="table"], [data-testid="compare-inspector"] [role="grid"]').count()) >= 1, Object.fromEntries(query(page)));

  await page.getByRole('tab', { name: 'Query' }).click();
  await page.waitForFunction(() => new URL(location.href).searchParams.get('inspectTab') === 'query');
  await shot(page, 'tools-inspect-query');
  check('Inspect query lists the PromQL', (await page.locator('[data-testid="compare-inspector"] pre').count()) > 5, undefined);
  await page.getByRole('tab', { name: 'Change' }).click();
  await page.waitForSelector('text=Your role is read-only');
  await shot(page, 'tools-change-viewer');
  check('the editing workflow tells a Viewer what is true', (await page.getByRole('link', { name: /Grafana's editor/ }).count()) === 0, undefined);
  await monitors.get(page).navigate(() => page.goBack());
  await monitors.get(page).navigate(() => page.goBack());
  await monitors.get(page).navigate(() => page.goBack());
  await page.waitForFunction(() => !new URL(location.href).searchParams.has('inspect'));
  check('Back closes the inspector step by step', (await page.locator('[data-testid="compare-inspector"]').count()) === 0, undefined);

  await page.getByRole('button', { name: 'Share' }).click();
  const link = await page.locator('#share-link').inputValue();
  await shot(page, 'tools-share');
  check('Share is labelled custom', (await page.getByText("Custom tool, not Grafana's Share").count()) === 1, link);
  const shared = await open(1440, 1000);
  from = offset();
  await shared.log.datasource.navigate(() => shared.page.goto(link, { waitUntil: 'domcontentloaded' }));
  await shared.page.waitForSelector('[data-host="scenes"][data-focus="Network"]');
  await settle(shared.page);
  check('a shared link reproduces focus and range in a clean session', query(shared.page).get('from') === new URL(link).searchParams.get('from') && upstream(from).length === commonQueries.length + networkQueries.length, Object.fromEntries(query(shared.page)));
  await shared.log.datasource.drain();
  check('shared link has no page or console errors in a clean session', shared.log.errors.length === 0, shared.log.errors);
  await shared.browser.close();

  for (const focus of FOCUSES.slice(1)) {
    await goto(page, `?focus=${focus}`);
    check(`${focus} has a substantial internal domain page`, await page.locator('.lab-domain .domain-section').count() >= 2, undefined);
    check(`${focus} has resource/page links`, await page.locator('.lab-domain a').count() >= 2, undefined);
    const views = page.locator('.domain-tabs a');
    if (await views.count() > 1) {
      const beforePage = query(page).get('view');
      const beforeTime = query(page).get('from');
      await views.nth(1).click();
      await settle(page);
      check(`${focus} subpage keeps time`, query(page).get('from') === beforeTime, Object.fromEntries(query(page)));
      await monitors.get(page).navigate(() => page.goBack());
      await settle(page);
      check(`${focus} Back restores subpage`, query(page).get('view') === beforePage, undefined);
    }
    const resource = page.locator('.lab-domain a[href*="entity="]').first();
    if (await resource.count()) {
      await resource.click();
      await settle(page);
      check(`${focus} drills into a resource`, Boolean(query(page).get('entity')), Object.fromEntries(query(page)));
      await monitors.get(page).navigate(() => page.reload({ waitUntil: 'domcontentloaded' }));
      await settle(page);
      check(`${focus} resource survives reload`, Boolean(await page.locator('.lab-domain[data-entity]').count()), undefined);
    }
    await shot(page, `${focus.toLowerCase()}-detail-1440`, true);
  }

  await goto(page, '?focus=Network');
  const nativePlots = await page.locator('[data-chart] .uplot').count();
  check('declared Network charts actually draw using the native timeseries plugin', nativePlots > 0, { nativePlots });

  if (process.env.LIVE_SMOKE === '1') {
    await goto(page, '?source=live');
    check('direct live entry uses the moving default window', query(page).get('from') === 'now-24h', Object.fromEntries(query(page)));
    await clickSystem(page, 'Network');
    await page.waitForSelector('[data-host="scenes"][data-focus="Network"]');
    check('live source survives focus changes', query(page).get('source') === 'live', undefined);
  }

  await log.datasource.drain();
  check('datasource responses and transport are valid', log.datasource.transportOk && log.datasource.evaluationOk, { responses: log.datasource.responses, failures: log.datasource.failures, inFlight: log.datasource.inFlight });
  check('no page or console errors', log.errors.length === 0, log.errors.slice(0, 5));
  await browser.close();
}

async function narrow(width, height, name) {
  const { browser, page, log } = await open(width, height);
  for (const focus of FOCUSES) {
    await goto(page, `?focus=${focus}`);
    await shot(page, `${focus.toLowerCase()}-${name}`);
    const layout = await page.evaluate(async () => {
      const band = document.querySelector('[data-testid="pinned-band"]');
      const overflow = document.documentElement.scrollWidth - window.innerWidth;
      const items = [...band.querySelectorAll('[data-system]')].filter((item) => {
        const rect = item.getBoundingClientRect();
        return rect.width > 0 && rect.right <= window.innerWidth + 1 && rect.left >= -1;
      }).length;
      window.scrollTo(0, 700);
      await new Promise((resolve) => setTimeout(resolve, 200));
      const rect = band.getBoundingClientRect();
      return { overflow, items, top: rect.top, height: rect.height, share: rect.height / window.innerHeight };
    });
    await shot(page, `${focus.toLowerCase()}-${name}-scrolled`);
    check(`${focus} at ${width}: no sideways overflow, all eight systems reachable, band stays pinned`, layout.overflow <= 0 && layout.items >= 8 && layout.top <= 110, layout);
    check(`${focus} at ${width}: pinned band leaves most of the screen`, layout.share < 0.3, { share: Number(layout.share.toFixed(2)) });
  }
  await log.datasource.drain();
  check(`datasource responses and transport are valid at ${width}`, log.datasource.transportOk && log.datasource.evaluationOk, { responses: log.datasource.responses, failures: log.datasource.failures, inFlight: log.datasource.inFlight });
  check(`no page or console errors at ${width}`, log.errors.length === 0, log.errors.slice(0, 5));
  await browser.close();
}

const run = process.argv[2] ?? 'all';
if (!['all', 'desktop', 'narrow'].includes(run)) throw new Error(`Unknown proof mode: ${run}. Use all, desktop, or narrow.`);
fs.mkdirSync(OUT, { recursive: true });
fs.rmSync(path.join(OUT, 'browser-proof.json'), { force: true });
try {
  if (run === 'all' || run === 'desktop') await desktop();
  if (run === 'all' || run === 'narrow') {
    await narrow(728, 1000, '728');
    await narrow(390, 844, '390');
  }
} catch (error) {
  check('browser proof completed', false, { message: error.message, stack: error.stack });
} finally {
  for (const datasource of monitors.values()) {
    await datasource.drain();
    check('all opened pages have valid datasource responses', datasource.transportOk && datasource.evaluationOk, { responses: datasource.responses, failures: datasource.failures, inFlight: datasource.inFlight, abortedNavigations: datasource.abortedNavigations });
  }
  const cleanup = await Promise.allSettled([...browsers].map((browser) => browser.close()));
  cleanup.forEach((result) => { if (result.status === 'rejected') check('browser closes', false, result.reason.message); });
  fs.writeFileSync(path.join(OUT, 'browser-proof.json'), JSON.stringify({ base: BASE, at: new Date().toISOString(), results }, null, 2));
}
const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length} of ${results.length} passed`);
process.exitCode = failed.length || !results.length ? 1 : 0;
