// Builds the harness and captures it in Chromium. Args: name=url-query pairs. Output goes to shared/evidence unless OUT is set.
import { build } from 'esbuild';
import fs from 'node:fs';
import { chromium, launchOptions } from '../../checks/playwright.mjs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

const specs = process.argv.slice(2);
if (!specs.length) throw new Error('Provide at least one capture spec: name|query|width|height|full');
const site = path.join(here, 'out');
fs.mkdirSync(site, { recursive: true });
await build({ entryPoints: [path.join(here, 'harness.tsx')], bundle: true, outfile: path.join(site, 'bundle.js'), format: 'iife', platform: 'browser', target: 'es2022', jsx: 'automatic', define: { 'process.env.NODE_ENV': '"development"' }, loader: { '.json': 'json' } });
fs.writeFileSync(path.join(site, 'index.html'), `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:radial-gradient(760px 240px at 8% -6%,rgba(63,230,196,.055),transparent 70%),radial-gradient(900px 300px at 94% -8%,rgba(77,176,255,.075),transparent 72%),#060B11;font-family:Inter,sans-serif}.chart-stub{display:grid;place-items:center;height:100%;border-radius:14px;color:#7E92A4;font:12px Inter,sans-serif;box-shadow:inset 0 0 0 1px rgba(150,205,245,.10)}</style><div id="root"></div><script src="bundle.js"></script>`);

const out = process.env.OUT ?? path.join(here, '../evidence');
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch(launchOptions);
const problems = [];
try {
  for (const spec of specs) {
    const [name, query, width = '1440', height = '900', full = 'full'] = spec.split('|');
    let page;
    try {
      page = await browser.newPage({ viewport: { width: Number(width), height: Number(height) }, deviceScaleFactor: 1 });
      page.on('pageerror', (e) => problems.push(`${name}: ${e.message}`));
      page.on('console', (m) => m.type() === 'error' && problems.push(`${name}: ${m.text()}`));
      const target = pathToFileURL(path.join(site, 'index.html'));
      target.search = query ?? '';
      await page.goto(target.href);
      await page.waitForSelector('[data-view]');
      await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: full === 'full' });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (overflow > 0) problems.push(`${name}: page overflows sideways by ${overflow}px`);
    } catch (error) {
      problems.push(`${name}: capture failed: ${error.message}`);
    } finally {
      await page?.close().catch((error) => problems.push(`${name}: close failed: ${error.message}`));
    }
  }
} finally {
  await browser.close().catch((error) => problems.push(`Browser close failed: ${error.message}`));
}
if (problems.length) {
  console.error(problems.join('\n'));
  process.exitCode = 1;
} else {
  console.log('no page errors, console errors or sideways overflow');
}
