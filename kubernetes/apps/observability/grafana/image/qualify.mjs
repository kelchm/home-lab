// Qualify a built image through real Grafana APIs and Chromium, with an owned disposable database.
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const image = process.argv[2]; assert.ok(image, 'Usage: node qualify.mjs IMAGE');
const docker = process.env.DOCKER ?? 'docker';
const stock = fs.readFileSync(path.join(here, 'Dockerfile'), 'utf8').match(/^ARG GRAFANA_IMAGE=(.+)$/m)[1];
const definition = JSON.parse(fs.readFileSync(path.join(here, 'theme.json')));
const lock = JSON.parse(fs.readFileSync(path.join(here, 'source-lock.json')));
// Production's synchronous plugin installation settings as of this qualification baseline.
const plugins = 'victoriametrics-metrics-datasource,victoriametrics-logs-datasource@0.33.1@https://grafana.com/api/plugins/victoriametrics-logs-datasource/versions/0.33.1/download';
const architecture = execFileSync(docker, ['image', 'inspect', image, '--format', '{{.Architecture}}'], { encoding: 'utf8' }).trim();
const nonce = randomBytes(6).toString('hex');
const name = `grafana-theme-${nonce}`;
const volume = `${name}-data`;
const output = path.join(here, 'test-results', nonce);
const provisioning = path.join(output, 'provisioning');
fs.mkdirSync(path.join(provisioning, 'datasources'), { recursive: true });
fs.writeFileSync(path.join(provisioning, 'datasources/testdata.yaml'), 'apiVersion: 1\ndatasources:\n  - name: Theme test data\n    uid: theme-testdata\n    type: grafana-testdata-datasource\n    access: proxy\n');
const password = randomBytes(24).toString('hex');
const auth = { authorization: `Basic ${Buffer.from(`admin:${password}`).toString('base64')}`, 'content-type': 'application/json' };
const report = { image, architecture, upstream: lock.version, checks: [], pageErrors: [], failedAssets: [] };
const run = (args) => execFileSync(docker, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
let base, port, browser, page;
const check = (name, detail = {}) => { report.checks.push({ name, ...detail }); console.log(name); };
async function backendDigest() {
  const file = path.join(output, 'backend.tmp');
  try {
    run(['cp', `${name}:/usr/share/grafana/bin/grafana`, file]);
    const hash = createHash('sha256');
    for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
    return hash.digest('hex');
  } finally { fs.rmSync(file, { force: true }); }
}
function imageReport(file) {
  const target = path.join(output, file);
  run(['cp', `${name}:/usr/share/grafana/homelab-theme/${file}`, target]);
  return JSON.parse(fs.readFileSync(target));
}
async function start(ref) {
  // Stop the old page's background requests before deliberately taking its server away.
  // Keep this browser/context so cookies and the HTTP cache survive each artifact switch.
  if (page) await page.goto('about:blank');
  try { run(['rm', '-f', name]); } catch {}
  run(['volume', 'create', '--label', 'home-lab.grafana-theme-test=true', volume]);
  run(['run', '-d', '--platform', `linux/${architecture}`, '--name', name, '--label', 'home-lab.grafana-theme-test=true', '--read-only', '--tmpfs', '/tmp:mode=1777', '--cap-drop', 'ALL',
    '-p', `127.0.0.1:${port ?? ''}:3000`, '-v', `${volume}:/var/lib/grafana`, '-v', `${provisioning}:/etc/grafana/provisioning:ro`,
    '-e', 'GF_AUTH_ANONYMOUS_ENABLED=true', '-e', 'GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer', '-e', `GF_SECURITY_ADMIN_PASSWORD=${password}`,
    '-e', 'GF_AUTH_BASIC_ENABLED=true', '-e', `GF_PLUGINS_PREINSTALL_SYNC=${plugins}`, '-e', 'GF_ANALYTICS_REPORTING_ENABLED=false',
    '-e', 'GF_ANALYTICS_CHECK_FOR_UPDATES=false', '-e', 'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false', '-e', 'GF_NEWS_NEWS_FEED_ENABLED=false',
    '-e', 'GF_SECURITY_ASSET_SRI_CHECKS_ENABLED=true', ref]);
  port = run(['port', name, '3000/tcp']).match(/:(\d+)$/)[1]; base = `http://127.0.0.1:${port}`;
  for (let n = 0; n < 400; n++) {
    try { const response = await fetch(base + '/api/health'); if (response.ok && (await response.json()).database === 'ok') return; } catch {}
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error('Grafana did not start');
}
async function api(url, method = 'GET', body) {
  const response = await fetch(base + url, { method, headers: auth, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await response.text(); assert.ok(response.ok, `${method} ${url}: ${response.status} ${text}`); return text ? JSON.parse(text) : {};
}
const pref = (theme) => api('/api/user/preferences', 'PATCH', { theme });
async function readTheme() {
  return page.evaluate(async () => { const { config } = await System.import('@grafana/runtime'); return { name: config.theme2.name, canvas: config.theme2.colors.background.canvas, isDark: config.theme2.isDark, palette: config.theme2.visualization.palette, bootId: window.grafanaBootData.user.theme }; });
}
async function dashboard(query = '') {
  await page.goto(base + '/d/theme-qualification/proof' + query, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('data-testid Panel header Native palette').waitFor();
  await page.waitForFunction(() => document.querySelectorAll('.uplot canvas').length > 0);
  await page.waitForLoadState('networkidle'); return readTheme();
}
try {
  await start(image);
  const candidateBackend = await backendDigest();
  const frontendParity = imageReport('frontend-parity.json'), backendParity = imageReport('backend-parity.json');
  assert.equal(frontendParity.status, 'identical'); assert.ok(frontendParity.files > 0); assert.equal(backendParity.status, 'identical');
  assert.equal(imageReport('backend-custom.json').sha256, candidateBackend); check('Image carries passing frontend/backend parity gates', { frontendParity, backendParity });
  const health = await api('/api/health'); assert.equal(health.commit, lock.commit); assert.equal(health.version, lock.version + '+homelab.1'); check('Registered image reports its actual source and version', { health });
  const installed = await api('/api/plugins');
  const pluginVersions = {};
  for (const id of ['victoriametrics-metrics-datasource', 'victoriametrics-logs-datasource']) {
    const plugin = installed.find((p) => p.id === id); assert.ok(plugin, `Plugin preinstall failed: ${id}`); assert.equal(plugin.signature, 'valid');
    pluginVersions[id] = plugin.info.version;
  }
  check('Production-shaped synchronous plugin preinstall succeeds', { pluginVersions });
  await api('/api/dashboards/db', 'POST', { dashboard: { uid: 'theme-qualification', title: 'Theme qualification', schemaVersion: 39, time: { from: 'now-1h', to: 'now' }, panels: [{ id: 1, type: 'timeseries', title: 'Native palette', gridPos: { x: 0, y: 0, w: 24, h: 12 }, datasource: { uid: 'theme-testdata', type: 'grafana-testdata-datasource' }, targets: [{ refId: 'A', scenarioId: 'random_walk', seriesCount: 3 }], fieldConfig: { defaults: { color: { mode: 'palette-classic' }, custom: { lineWidth: 2, showPoints: 'never' } }, overrides: [] }, options: { legend: { showLegend: true, displayMode: 'list', placement: 'bottom' } } }] }, overwrite: true });
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage();
  page.on('pageerror', (e) => report.pageErrors.push(String(e)));
  page.on('requestfailed', (r) => { if (r.url().startsWith(base + '/public/') && r.failure()?.errorText !== 'net::ERR_ABORTED') report.failedAssets.push({ url: r.url(), error: r.failure()?.errorText }); });
  page.on('response', (r) => { if (r.url().startsWith(base + '/public/') && r.status() >= 400) report.failedAssets.push({ url: r.url(), status: r.status() }); });
  const initial = await dashboard(); assert.equal(initial.name, definition.name); assert.equal(initial.bootId, definition.id); assert.equal(initial.canvas, definition.colors.background.canvas); assert.deepEqual(initial.palette, definition.visualization.palette); check('Native startup and chart palette', { theme: initial });
  assert.equal(await page.evaluate(() => window.__grafanaAssetSriChecksEnabled), true); check('Asset integrity checking is enabled');
  const appPlugins = await page.evaluate(() => Object.keys(window.grafanaBootData.settings.apps ?? {}));
  assert.ok(!appPlugins.includes('homelab-aurora-app')); check('No runtime theme adapter is installed', { appPlugins });
  const pixels = await page.evaluate((colors) => {
    const counts = Object.fromEntries(colors.map((c) => [c.toLowerCase(), 0]));
    for (const canvas of document.querySelectorAll('.uplot canvas')) {
      const ctx = canvas.getContext('2d'); if (!ctx) continue;
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] === 0) continue;
        const hex = '#' + [data[i], data[i + 1], data[i + 2]].map((v) => v.toString(16).padStart(2, '0')).join('');
        if (hex in counts) counts[hex]++;
      }
    }
    return counts;
  }, definition.visualization.palette.slice(0, 3));
  assert.ok(Object.values(pixels).every((n) => n > 10), JSON.stringify(pixels)); check('Native chart paints all three Aurora series colors', { pixels });
  await page.screenshot({ path: path.join(output, 'anonymous-desktop.png') });
  await page.goto(base + '/login');
  await page.getByTestId('data-testid Username input field').fill('admin');
  await page.getByTestId('data-testid Password input field').fill(password);
  await page.getByTestId('data-testid Login button').click(); await page.waitForURL((u) => !u.pathname.startsWith('/login'));
  await pref('light'); await page.goto(base + '/profile'); await page.waitForLoadState('networkidle');
  await page.locator('#shared-preferences-theme-select').click();
  await page.getByRole('option', { name: definition.name, exact: true }).click();
  await Promise.all([page.waitForNavigation({ waitUntil: 'networkidle' }), page.getByRole('button', { name: 'Save preferences', exact: true }).click()]);
  assert.equal((await api('/api/user/preferences')).theme, definition.id); assert.equal((await dashboard()).name, definition.name); check('Native selector saves and reloads the independent Aurora ID');
  for (const id of ['light', 'dark']) { await pref(id); const value = await dashboard(); assert.equal(value.isDark, id === 'dark'); assert.notEqual(value.name, definition.name); check(`Stock ${id} remains selectable`); }
  await api('/api/org/preferences', 'PATCH', { theme: 'tron' }); await pref(''); assert.notEqual((await dashboard()).name, definition.name);
  await pref('light'); assert.equal((await dashboard()).isDark, false); check('User preference overrides organization preference');
  await api('/api/org/preferences', 'PATCH', { theme: '' }); await pref(''); assert.equal((await dashboard()).name, definition.name); check('Default theme is inherited when preferences are cleared');
  await pref('light'); assert.equal((await dashboard('?theme=' + definition.id)).name, definition.name); check('Native URL override accepts the registered ID');
  const invalid = await fetch(base + '/api/user/preferences', { method: 'PATCH', headers: auth, body: JSON.stringify({ theme: 'unregistered-theme' }) }); assert.equal(invalid.status, 422); check('Unregistered IDs remain rejected');
  await pref(definition.id);
  const cdp = await context.newCDPSession(page); await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 });
  for (let i = 0; i < 5; i++) assert.equal((await dashboard()).name, definition.name);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 }); check('Five throttled reloads use the registered theme');
  await page.setViewportSize({ width: 390, height: 844 }); await dashboard(); await page.screenshot({ path: path.join(output, 'phone.png') }); await page.setViewportSize({ width: 1440, height: 1000 });
  await start(stock); assert.equal(await backendDigest(), backendParity.sha256); check('Control-build parity matches the actual official runtime binary', { sha256: backendParity.sha256 });
  const rollback = await dashboard(); assert.notEqual(rollback.name, definition.name); assert.equal((await api('/api/user/preferences')).theme, definition.id); check('Stock image works with the custom theme ID still stored in the same database', { theme: rollback });
  await start(image); assert.equal((await dashboard()).name, definition.name); check('Re-upgrade restores Aurora from the same database');
  assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.failedAssets, []); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.error = String(error); await page?.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {}); throw error;
} finally {
  await browser?.close();
  try { fs.writeFileSync(path.join(output, 'grafana.log'), run(['logs', name])); } catch {}
  try { run(['rm', '-f', name]); } catch {}
  try { run(['volume', 'rm', volume]); } catch {}
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n'); console.log(path.join(output, 'report.json'));
}
