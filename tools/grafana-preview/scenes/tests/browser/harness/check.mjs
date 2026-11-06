// Drives the harness, never Grafana: layout, pinning and tool flows with real components. It writes screenshots and results to evidence/harness.
import fs from 'node:fs';
import path from 'node:path';
import { chromium, launchOptions } from '../../../../checks/playwright.mjs';

const OUT = path.resolve(process.env.EVIDENCE ?? path.resolve(import.meta.dirname, '../../../evidence/harness'));
const BASE = `http://127.0.0.1:${process.env.PORT ?? 3419}/a/homelab-observability-app/explore`;
const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail === undefined ? '' : ` :: ${JSON.stringify(detail)}`}`);
};

fs.mkdirSync(OUT, { recursive: true });
fs.rmSync(path.join(OUT, 'results.json'), { force: true });
let browser;

async function open(width, height, query = '') {
  const context = await browser.newContext({ viewport: { width, height } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message.slice(0, 200)));
  page.on('console', (message) => {
    // The standalone harness has no Grafana boot object. Keep all resource and component errors.
    if (message.type() === 'error' && message.text() !== 'window.grafanaBootData was not set by the time config was initialized') {
      errors.push(message.text().slice(0, 200));
    }
  });
  page.on('requestfailed', (request) => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
  page.on('response', (response) => { if (response.status() >= 400) errors.push(`${response.status()} ${response.url()}`); });
  await page.goto(`${BASE}${query}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-view="briefing"][data-freshness="fresh"]', { timeout: 20000 });
  await page.waitForTimeout(600);
  return { context, page, errors };
}

const shot = (page, name, fullPage = false) => page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage });
const columns = (page) =>
  page.evaluate(() => {
    const [activity, review] = ['activity', 'review'].map((card) => document.querySelector(`[data-card="${card}"]`).getBoundingClientRect());
    return { activity: Math.round(activity.width), review: Math.round(review.width), sideBySide: Math.abs(activity.top - review.top) < 2 && review.left > activity.right };
  });

try {
  browser = await chromium.launch(launchOptions);
  // Content width is viewport minus 32 px of harness padding, which stands in for Grafana's own canvas padding. A docked menu is 300 px more.
  for (const [viewport, content, expected] of [[1740, 1708, true], [1440, 1408, true], [1132, 1100, true], [940, 908, true], [920, 888, false]]) {
    const { context, page, errors } = await open(viewport, 900);
    const layout = await columns(page);
    check(`${content} px of content ${expected ? 'keeps Activity and Review as peers' : 'stacks them'}`, layout.sideBySide === expected, layout);
    if (viewport === 1132) {
      await shot(page, 'overview-content-1100');
    }
    check('harness page has no resource or console errors', errors.length === 0, errors);
    await context.close();
  }

  {
    const { context, page, errors } = await open(1440, 1000);
    await shot(page, 'overview-1440', true);
    const natural = await page.evaluate(() =>
      ['activity', 'review', 'machines', 'checks'].map((card) => {
        const element = document.querySelector(`[data-card="${card}"]`);
        const clipped = [...element.querySelectorAll('*')].filter((node) => /auto|scroll/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight + 2 && node.clientHeight > 40);
        return { card, height: Math.round(element.getBoundingClientRect().height), clipped: clipped.length };
      })
    );
    check('natural height with nothing scrolled inside a card', natural.every((item) => item.clipped === 0), natural);

    await page.locator('details.checks summary').click();
    await page.waitForTimeout(200);
    const open10 = await page.locator('details.checks [data-check]').count();
    const machinesTop = await page.evaluate(() => document.querySelector('[data-card="machines"]').getBoundingClientRect().top);
    await shot(page, 'overview-checks-open', true);
    check('the checks disclosure opens in place and pushes Machines down', open10 === 10 && machinesTop > 1100, { rows: open10, machinesTop: Math.round(machinesTop) });
    await page.locator('details.checks summary').click();

    const pinned = await page.evaluate(async () => {
      const band = document.querySelector('[data-testid="pinned-band"]');
      window.scrollTo(0, 900);
      await new Promise((resolve) => setTimeout(resolve, 200));
      const rect = band.getBoundingClientRect();
      return { top: rect.top, height: Math.round(rect.height), scrollY: window.scrollY, picker: band.querySelector('[data-testid*="TimePicker"]') !== null, systems: band.querySelectorAll('[data-system]').length };
    });
    check('the band pins at the chrome height with the time picker and all eight systems', pinned.scrollY > 0 && pinned.top === 48 && pinned.picker && pinned.systems === 8, pinned);
    await page.evaluate(() => window.scrollTo(0, 0));

    await page.locator('[data-card="review"]').hover({ position: { x: 120, y: 200 } });
    await page.waitForTimeout(200);
    const overlap = await page.evaluate(() => {
      const tool = document.querySelector('[data-card="activity"] button[aria-label^="Tools"]').getBoundingClientRect();
      const pill = document.querySelector('[data-card="activity"] .pill').getBoundingClientRect();
      return tool.bottom > pill.top && tool.top < pill.bottom && tool.right > pill.left && tool.left < pill.right;
    });
    await shot(page, 'tool-hover');
    check('the tool button does not cover the status pill', !overlap, undefined);

    await page.getByRole('button', { name: 'Tools for What happened' }).click();
    await page.waitForSelector('[role="menu"]');
    const items = await page.locator('[role="menu"] [role="menuitem"]').allInnerTexts();
    await shot(page, 'tool-menu');
    check('the card menu lists read-only tools, each custom one labelled', items.join('|') === 'Inspect data (custom)|Inspect query (custom)|Share view (custom)|Edit (custom)…', items);

    await page.locator('[role="menu"] [role="menuitem"]', { hasText: 'Inspect data' }).click();
    await page.waitForSelector('[data-testid="compare-inspector"]');
    await page.waitForTimeout(500);
    await shot(page, 'inspect-data');
    const table = await page.locator('[data-testid="compare-inspector"] [role="table"], [data-testid="compare-inspector"] [role="grid"]').count();
    check('Inspect data draws a table and names itself as custom', table >= 1 && (await page.getByText("Custom inspector, not Grafana's").count()) === 1, { table });

    await page.getByRole('tab', { name: 'Query' }).click();
    await page.waitForSelector('[data-testid="compare-inspector"] pre');
    await shot(page, 'inspect-query');
    check('Inspect query shows PromQL and why Explore is absent', (await page.locator('[data-testid="compare-inspector"] pre').count()) > 5 && (await page.getByText(/datasources:explore/).count()) === 1, undefined);

    await page.getByRole('tab', { name: 'Change' }).click();
    await page.waitForSelector('text=Your role is read-only');
    await shot(page, 'inspect-change-viewer');
    check('the editing workflow is honest for a Viewer', (await page.getByRole('link', { name: /Grafana's editor/ }).count()) === 0, undefined);

    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    check('Escape closes the inspector and clears its URL keys', !new URL(page.url()).searchParams.has('inspect'), page.url().split('?')[1]);

    await page.getByRole('button', { name: 'Share' }).click();
    await page.waitForSelector('#share-link');
    await shot(page, 'share');
    const link = await page.locator('#share-link').inputValue();
    check('Share offers a link to this focus and range', new URL(link).searchParams.get('from') === '2026-11-01T01:27:04.636Z', link);
    await page.keyboard.press('Escape');

    await page.locator('a[data-system="Network"]').click();
    await page.waitForSelector('[data-domain="net"]');
    await page.waitForTimeout(600);
    await shot(page, 'network-1440', true);
    await page.locator('[data-chart]').first().hover();
    await page.locator('[data-chart] button[aria-label^="Menu for panel"], [data-chart] button[data-testid*="panel menu"]').first().click();
    await page.waitForSelector('[role="menu"]');
    const chart = await page.locator('[role="menu"] [role="menuitem"]').allInnerTexts();
    await shot(page, 'chart-menu');
    check('a VizPanel shows the configured public menu, the same as a card', chart.join('|') === items.join('|'), chart);
    await page.keyboard.press('Escape');

    await page.getByRole('link', { name: 'Live' }).click();
    await page.waitForSelector('[data-host="scenes"][data-source="live"]');
    await page.waitForTimeout(600);
    const params = new URL(page.url()).searchParams;
    check('the Live link keeps focus and the selected window', params.get('source') === 'live' && params.get('focus') === 'Network' && params.get('from') === '2026-11-01T01:27:04.636Z', Object.fromEntries(params));
    check('harness page has no resource or console errors', errors.length === 0, errors);
    await context.close();
  }

  {
    const { context, page, errors } = await open(1440, 1000, '?inspect=machines&inspectTab=data');
    await page.waitForSelector('[data-testid="compare-inspector"]');
    await page.waitForTimeout(800);
    const table = await page.locator('[data-testid="compare-inspector"] [role="table"], [data-testid="compare-inspector"] [role="grid"]').count();
    check('a reload with the inspector open still draws the table', table >= 1 && errors.length === 0, { table, errors: errors.slice(0, 3) });
    check('harness page has no resource or console errors', errors.length === 0, errors);
    await context.close();
  }

  for (const [width, height] of [[728, 1000], [390, 844]]) {
    for (const focus of ['Overview', 'Network']) {
      const { context, page, errors } = await open(width, height, `?focus=${focus}`);
      await shot(page, `${focus.toLowerCase()}-${width}`);
      const layout = await page.evaluate(async () => {
        const band = document.querySelector('[data-testid="pinned-band"]');
        const overflow = document.documentElement.scrollWidth - window.innerWidth;
        const systems = [...band.querySelectorAll('[data-system]')].filter((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.left >= -1 && rect.right <= window.innerWidth + 1;
        }).length;
        window.scrollTo(0, 500);
        await new Promise((resolve) => setTimeout(resolve, 200));
        const rect = band.getBoundingClientRect();
        return { overflow, systems, scrollY: window.scrollY, top: rect.top, height: Math.round(rect.height), share: Number((rect.height / window.innerHeight).toFixed(2)) };
      });
      await shot(page, `${focus.toLowerCase()}-${width}-scrolled`);
      check(`${focus} at ${width}: no sideways overflow, eight systems, pinned, no errors`, layout.overflow <= 0 && layout.systems === 8 && layout.scrollY > 0 && layout.top === 48 && errors.length === 0, { ...layout, errors: errors.slice(0, 3) });
      check(`${focus} at ${width}: band leaves most of the screen`, layout.share < 0.3, { share: layout.share });
      check('harness page has no resource or console errors', errors.length === 0, errors);
    await context.close();
    }
  }

} catch (error) {
  check('harness completed', false, { message: error.message, stack: error.stack });
} finally {
  try { await browser?.close(); }
  catch (error) { check('browser closes', false, error.message); }
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ at: new Date().toISOString(), results }, null, 2));
}
const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length} of ${results.length} passed`);
process.exitCode = failed.length || !results.length ? 1 : 0;
